# BFF Drill Readiness — Status Refresh and Unblock Guide: LOOP-AUTO-BFF-004 — FOLLOWUP-4

**Sidecar kind:** bff_handoff_packet (status refresh + unblock guide)
**Parent task:** LOOP-AUTO-BFF-004
**Sidecar task:** LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-4
**Prepared by:** Claude
**Date:** 2026-06-27
**Reviewer:** Claude2

---

## Purpose

FOLLOWUP-3 was approved and merged with the consolidated pre-drill checklist,
evidence file templates, and maturity statement templates. Since then, the majority
of LOOP-AUTO-BFF-004's dependency tasks have completed. This packet:

1. Refreshes the dependency status as of 2026-06-27
2. Updates the surface gap registry to reflect completed work
3. Documents the one remaining blocker (EVO-005 process gap)
4. Provides the immediate action plan to unblock and start drills

Prior packets remain the normative reference for drill execution procedures:
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF.md` — full gap analysis and operator journey
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` — filter gap resolution spec
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md` — consolidated pre-drill packet (use as primary drill reference)

This packet does **not** modify any L1 canonical policy, `ai-status.json`, the
loop registry, or any BFF implementation file.

---

## 1. Dependency Status Snapshot (as of 2026-06-27)

Six of seven dependencies are now `done`. One is `blocked`.

| Task | Title | Status | PR / Evidence |
|---|---|---|---|
| LOOP-AUTO-SRC-004 | Wire SourceHealth truth into persona panels | **done** | PR #2452; evidence: `docs/deployment/evidence/loop-auto-src-004/README.md` |
| LOOP-AUTO-RT-005 | Produce runtime fleet evidence packet | **done** | done |
| LOOP-AUTO-DEP-004 | Split promotion and deployment BFF truth by stage | **done** | PR #2451; 5-stage split (approval/plan/saga/binding/runtime_fleet) delivered |
| LOOP-AUTO-TEL-005 | Add telemetry incident replay and operator evidence | **done** | done |
| LOOP-AUTO-KNOW-006 | Add consultation workflow executor | **done** | PR #2462; `services/consultation`; 29 tests passed |
| LOOP-AUTO-BFF-001 | Add loop health read model | **done** | PR #2423; `GET /api/v1/loops` and `GET /api/v1/loops/{loop_id}` live |
| LOOP-AUTO-BFF-003 | Label seed snapshot registry scheduled and live truth | **done** | archived 2026-06-27T14:27:56Z; `truth_source_label` field deployed |
| **LOOP-AUTO-EVO-005** | **Prove evolution rollback and follow-through** | **blocked** | PR 2475 merged CI green; review file committed; approve step missing — see §2 |

**Net status:** BFF-004 is one unblock step away from being fully ready to execute.

---

## 2. EVO-005 Process Gap and Unblock Path

### 2.1 What Happened

LOOP-AUTO-EVO-005 is in `blocked` status with `waiting_for: Claude`. The full
history:

- Claude2 (owner) implemented the rollback follow-through work
- PR 2475 merged, CI green
- Claude (reviewer) committed a review approval to
  `docs/deployment/evidence/loop-auto-evo-005/review-claude.md`
- The review file verdict is **APPROVED** with 20 tests verified
- However, the formal `AI_NAME=Claude ./scripts/ai-status.sh approve` command
  was never run
- The task remained in `blocked` state instead of progressing to `review_approved`

### 2.2 Why `approve` Cannot Be Run Directly

`scripts/ai-status.sh approve` requires the task to be in `review` status. The
current `blocked` state prevents direct approval.

### 2.3 Required Fix Sequence

**Step 1 (Claude2, as owner):** Transition EVO-005 from `blocked` back to `review`
by running handoff again:

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh handoff LOOP-AUTO-EVO-005 Claude \
  "Re-handoff to Claude for formal approve — PR 2475 merged, review file at \
   docs/deployment/evidence/loop-auto-evo-005/review-claude.md already shows APPROVED"
```

**Step 2 (Claude, as reviewer):** Run the formal approve command:

```bash
AI_NAME=Claude \
  REVIEW_FILE=docs/deployment/evidence/loop-auto-evo-005/review-claude.md \
  REVIEW_NOTES_ZH="審查通過：20 tests pass||AC-1 E2E 凍結→rollback-followthrough→RuntimeManagerService.rollback() 驗證完成||AC-2 BFF observation-report 暴露全部五個 stage||AC-3 failure paths 明確 surfaced 阻塞原因" \
  ./scripts/ai-status.sh approve LOOP-AUTO-EVO-005 \
  "Review complete — 20 tests pass, all 3 ACs verified; returning to Claude2 for finalization"
```

**Step 3 (Claude2, as owner):** Finalize EVO-005:

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh done LOOP-AUTO-EVO-005 \
  "Finalized: PR 2475 merged, review approved, evidence committed"
```

After Step 3, all BFF-004 dependencies are `done` and Claude2 may begin drills.

---

## 3. Updated Surface Gap Registry

Based on completed dependency tasks, the following gaps from FOLLOWUP-3 §1 are
now **likely resolved**. Claude2 must verify each with the quick check command
before proceeding.

### 3.1 Surface Gaps — Updated Status

| Gap ID | Route | Blocking drill | Owned by | Status update |
|---|---|---|---|---|
| SG-001 | `GET /api/v1/personas/{id}/source-health` | Drill 1 | SRC-004 | **Likely resolved** — SRC-004 done (PR #2452) |
| SG-002 | `GET /api/v1/source-connectors` (required fields) | Drill 1 | SRC-004 | **Likely resolved** — SRC-004 done; verify fields |
| SG-003 | `GET /api/v1/loops` | Both drills | BFF-001 | **Likely resolved** — BFF-001 done (PR #2423) |
| SG-004 | `GET /api/v1/loops/{loop_id}` | Both drills | BFF-001 | **Likely resolved** — BFF-001 done (PR #2423) |
| SG-005 | `truth_source_label` field | Drill 1 | BFF-003 | **Likely resolved** — BFF-003 done (archived) |
| SG-006 | 5-stage deployment split fields | Drill 2 | DEP-004 | **Likely resolved** — DEP-004 done (PR #2451) |
| SG-007 | `dispatched_at`, `execution_result`, `blocked_reason` in EV-02 | Drill 2 | EVO-005 | **Still open** — EVO-005 blocked; unblock per §2 |

### 3.2 Filter Gaps — Status Unknown

| Gap ID | Route | Filter | Status |
|---|---|---|---|
| FG-001 | `GET /api/v1/incidents` | `runtime_id` | Unknown — verify via quick check below |
| FG-002 | `GET /api/v1/evolution-decisions` | `incident_id` | Unknown — verify via quick check below |

FG-001 and FG-002 may have been resolved as part of DEP-004 (Option A from
FOLLOWUP-2 §3) or may still require the fallback procedures from FOLLOWUP-2 §4.
Claude2 must run the quick check commands at drill start.

### 3.3 Pre-Drill Surface Verification Commands

Run these before starting drills to confirm gap resolution:

```bash
# SG-001: source-health sub-resource
curl -s "$BFF_BASE/api/v1/personas/$PERSONA_ID/source-health" | jq 'type'
# Expect: "array" (not null, not 404)

# SG-002: source-connectors with required fields
curl -s "$BFF_BASE/api/v1/source-connectors" | jq '.[0] | {last_fetch_at, last_push_at, failure_reason, truth_source_label}'
# Expect: all four keys present (values may be null for inactive connectors)

# SG-003 / SG-004: loop read model
curl -s "$BFF_BASE/api/v1/loops" | jq '.[0] | {loop_id, current_maturity}'
curl -s "$BFF_BASE/api/v1/loops/source_ingestion" | jq '{loop_id, current_maturity}'
# Expect: loop_id and current_maturity fields present

# SG-005: truth_source_label field
curl -s "$BFF_BASE/api/v1/personas/$PERSONA_ID/source-health" | jq '.[0].truth_source_label'
# Expect: non-null label string (e.g., "live", "scheduled", "registry")

# SG-006: 5-stage deployment split
curl -s "$BFF_BASE/api/v1/runtimes/$RUNTIME_ID/status" | \
  jq '{approval, plan, saga, binding, runtime_fleet}'
# Expect: all five keys present

# SG-007: evolution follow-through fields (only available after EVO-005 unblocked)
curl -s "$BFF_BASE/api/v1/evolution-decisions/$DECISION_ID" | \
  jq '{dispatched_at, execution_result, blocked_reason}'
# Expect: all three keys present

# FG-001: incidents runtime_id filter
curl -s "$BFF_BASE/api/v1/incidents?runtime_id=$RUNTIME_ID" | \
  jq '.meta.filter_applied // "NOT APPLIED"'
# Expect: "runtime_id" (resolved) or "NOT APPLIED" (use fallback per FOLLOWUP-2 §4.1)

# FG-002: evolution-decisions incident_id filter
curl -s "$BFF_BASE/api/v1/evolution-decisions?incident_id=$INCIDENT_ID" | \
  jq '.meta.filter_applied // "NOT APPLIED"'
# Expect: "incident_id" (resolved) or "NOT APPLIED" (use fallback per FOLLOWUP-2 §4.2)
```

---

## 4. Revised Go/No-Go Checklist

This replaces the FOLLOWUP-3 §2 checklist with a shorter version reflecting
completed dependencies. Crossed-out items are completed; remaining items must
be verified.

### 4.1 Drill 1 (Source-to-Health) — Revised Prerequisites

```
[x] SRC-004, BFF-001, BFF-003 all merged to dev
[ ] Verify SG-001 resolved: source-health returns HTTP 200 (see §3.3)
[ ] Verify SG-002 resolved: source-connectors has required 4 fields (see §3.3)
[ ] Verify SG-003 resolved: /api/v1/loops returns loop list (see §3.3)
[ ] Verify SG-004 resolved: /api/v1/loops/source_ingestion returns detail (see §3.3)
[ ] Verify SG-005 resolved: truth_source_label non-null in at least one response (see §3.3)
[ ] Test environment: dev deployment is current with merged PRs
    Verification: git log --oneline origin/dev | grep -E 'BFF-001|SRC-004|BFF-003'
```

### 4.2 Drill 2 (Runtime-to-Incident-to-Evolution) — Revised Prerequisites

```
[x] DEP-004, TEL-005, KNOW-006 all merged to dev
[ ] EVO-005 unblocked and done (follow §2 sequence)
[ ] Verify SG-006 resolved: 5-stage breakdown fields present (see §3.3)
[ ] Verify SG-007 resolved: EV-02 has dispatched_at, execution_result (see §3.3)
[ ] Verify FG-001 status: native filter or confirm fallback (see §3.3)
[ ] Verify FG-002 status: native filter or confirm fallback (see §3.3)
[ ] TEL-005 replay corpus available in test env
    Verification: curl -s "$BFF_BASE/api/v1/telemetry/corpus-status" | jq .
[ ] Test environment: dev deployment is current with merged PRs
    Verification: git log --oneline origin/dev | grep -E 'DEP-004|TEL-005|EVO-005|KNOW-006'
```

### 4.3 Filter Gap Decision (unchanged from FOLLOWUP-3 §2.3)

If FG-001/FG-002 are still pending at drill time:
- **Path A** (native filter available): record resolution task/commit in evidence
- **Path B** (fallback): use FOLLOWUP-2 §4.1 and §4.2; annotate evidence per §4.3;
  BFF-004 reaches `reconciled`, not `proven-live`

---

## 5. Immediate Action Plan for Claude2

Once the EVO-005 process gap (§2) is resolved:

1. **Confirm BFF-004 parent task is unblocked** in `ai-status.json`
2. **Run pre-drill surface verification** from §3.3 against test env
3. **Run Drill 1** — use FOLLOWUP-3 §3.1 evidence template
4. **Run Drill 2** — use FOLLOWUP-3 §3.2 evidence template (with filter gap
   annotation if FG-001/FG-002 not resolved)
5. **Select maturity statement** — use FOLLOWUP-3 §5 templates; outcome depends
   on whether both drills pass with native filters
6. **Run BFF-004 done** after PR merges and evidence is committed

Recommended execution order remains the same as FOLLOWUP-3 §4:
- Drill 1 has no dependency on Drill 2 surfaces; run Drill 1 as soon as SRC-004
  surface verification passes, even if EVO-005 is still resolving
- Drill 2 requires EVO-005 `done` for SG-007 to be satisfied

---

## 6. Evidence File Paths (reference)

Evidence from dependency tasks (already committed in dev):
- SRC-004: `docs/deployment/evidence/loop-auto-src-004/README.md`
- EVO-005: `docs/deployment/evidence/loop-auto-evo-005/review-claude.md`
  (review evidence; full task evidence should be committed by Claude2 in EVO-005 closeout)

Evidence files Claude2 must produce for BFF-004:
- Drill 1: `docs/deployment/evidence/LOOP-AUTO-BFF-004-drill1-source-health.md`
  (use FOLLOWUP-3 §3.1 template)
- Drill 2: `docs/deployment/evidence/LOOP-AUTO-BFF-004-drill2-runtime-incident-evolution.md`
  (use FOLLOWUP-3 §3.2 template)

---

## 7. Updated Risk Register

Changes from FOLLOWUP-3 §6:

| Risk | Previous likelihood | Updated likelihood | Note |
|---|---|---|---|
| Replay corpus (TEL-005) not in test env | Medium | Low | TEL-005 done; corpus should be present |
| EVO-005 follow-through fields missing | High (if EVO-005 incomplete) | **Medium** | EVO-005 `blocked`; unblock per §2; once done, SG-007 should resolve |
| FG-001/FG-002 rejected with 400 | Medium | Medium | DEP-004 done but filter gap ownership unverified; run §3.3 check first |
| `truth_source_label` absent | Medium | Low | BFF-003 done; verify SG-005 in §3.3 before declaring resolved |
| Consultation gate blocks drill path | Low | Low | KNOW-006 done; consultation gate deployed |
| Test env not current with merged PRs | Not listed | **Medium** | 7 PRs merged since FOLLOWUP-3; environment sync required before drills |

**New risk:** EVO-005 blocked state may persist if neither Claude2 nor Claude
acts on the §2 sequence. Chair-review should flag this if BFF-004 remains
idle after this packet is reviewed.

---

## 8. Cross-Reference Map (updated)

| Topic | Primary source | Update |
|---|---|---|
| Full gap analysis | HANDOFF §3 | 7 gaps; SG-001 through SG-006 likely resolved; SG-007 pending EVO-005 |
| Filter gap resolution spec | FOLLOWUP-2 §2 | FG-001/FG-002 status unknown; verify per §3.3 |
| Fallback procedures | FOLLOWUP-2 §4 | Unchanged; still applicable if FG-001/FG-002 pending |
| Operator journey steps | HANDOFF §4 | Unchanged |
| Frontend panel readiness | HANDOFF §5 | Dependencies now done; panels should be ready once env is synced |
| Acceptance checklist | HANDOFF §6 | Unchanged; AC-D1-1 through AC-D2-6 |
| Evidence file templates | FOLLOWUP-3 §3 | Still normative; use as-is |
| Maturity statement templates | FOLLOWUP-3 §5 | Still normative |
| EVO-005 unblock sequence | **This packet §2** | New; required before Drill 2 |
| Pre-drill surface verification | **This packet §3.3** | New; replaces FOLLOWUP-3 §1 gap checks |
| Revised go/no-go checklist | **This packet §4** | Replaces FOLLOWUP-3 §2; shorter given deps done |

---

## 9. Sidecar Scope Constraints

This packet is a support artifact only. It:

- Does **not** modify any L1 canonical policy file
- Does **not** modify `ai-status.json`, `current-work.md`, or any loop registry
- Does **not** implement any BFF route, filter handler, or evidence collection
- Does **not** change the parent task's acceptance criteria
- Refreshes status and provides unblock guidance based on completed dependency tasks
- Must be absorbed into the parent task's final evidence packet at
  LOOP-AUTO-BFF-004 closeout alongside all prior sidecar packets

---

## 10. Change Log

| Date | Author | Change |
|---|---|---|
| 2026-06-27 | Claude | Initial FOLLOWUP-4 packet: dependency status refresh (6 of 7 deps done), EVO-005 process gap analysis and unblock sequence, updated gap registry, revised go/no-go checklist, updated risk register, immediate action plan for Claude2 |
