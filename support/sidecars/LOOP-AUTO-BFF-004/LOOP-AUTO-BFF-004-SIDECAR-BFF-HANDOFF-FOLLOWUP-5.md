# BFF Drill Readiness — EVO-005 Reassignment Update and Revised Action Plan: LOOP-AUTO-BFF-004 — FOLLOWUP-5

**Sidecar kind:** bff_handoff_packet (reassignment status update + revised action plan)
**Parent task:** LOOP-AUTO-BFF-004
**Sidecar task:** LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-5
**Prepared by:** Claude
**Date:** 2026-06-27
**Reviewer:** Claude2

---

## Purpose

FOLLOWUP-4 was approved and merged with a dependency status refresh and the EVO-005
unblock sequence. Since then, EVO-005 underwent a task reassignment: it was moved from
Claude2 (owner) to Gemini2 (new owner) and its status reverted from `blocked` to `todo`.
This changes the unblock path described in FOLLOWUP-4 §2.

This packet:

1. Documents the EVO-005 reassignment and its implications for BFF-004
2. Provides the revised action plan for Gemini2 to implement and prove EVO-005
3. Provides the updated action plan for Claude2 to proceed with BFF-004 drills
4. Updates the surface gap registry to reflect the current state

Prior packets remain the normative reference for their respective topics:
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF.md` — full gap analysis and operator journey
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` — filter gap resolution spec
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md` — consolidated pre-drill packet (primary drill reference)
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md` — FOLLOWUP-4 unblock guide (superseded for §2 EVO-005 sequence only)

This packet does **not** modify any L1 canonical policy, `ai-status.json`, the
loop registry, or any BFF implementation file.

---

## 1. EVO-005 Reassignment: What Changed

### 1.1 Before FOLLOWUP-5

As documented in FOLLOWUP-4 §2, EVO-005 was:

| Field | Value |
|---|---|
| Status | `blocked` |
| Owner | Claude2 |
| Reviewer | Claude |
| Waiting for | Claude (formal approve transition) |
| Root cause | Formal `scripts/ai-status.sh approve` was never run despite review evidence being committed |

FOLLOWUP-4 provided a 3-step resolution sequence requiring Claude2 (owner) to re-handoff,
then Claude (reviewer) to approve, then Claude2 to finalize.

### 1.2 After FOLLOWUP-5 (current state as of 2026-06-27)

EVO-005 has been **reassigned** to Gemini2:

| Field | Value |
|---|---|
| Status | `todo` |
| Owner | Gemini2 |
| Reviewer | Claude |
| Waiting for | (none — available for fresh dispatch) |
| Effective change | Task reset to fresh dispatch rather than unblocking the prior blocked flow |

The prior blocked state (Claude2 → Claude approve) is no longer the active path.
Gemini2 must implement EVO-005 from scratch (or reuse the existing evidence corpus).

### 1.3 Existing Evidence That Gemini2 May Reuse

Evidence committed to the repo from the prior Claude2 implementation:

| Artifact | Path | Status |
|---|---|---|
| Test suite | `services/evolution/test_evo_005_rollback_followthrough.py` | Committed; 20 tests pass |
| Evidence README | `docs/deployment/evidence/loop-auto-evo-005/README.md` | Committed; architecture notes, failure-path table, stage-visibility table |
| Review document | `docs/deployment/evidence/loop-auto-evo-005/review-claude.md` | Committed; Claude's APPROVED verdict; AC-1, AC-2, AC-3 assessed |

The review notes are also present in `ai-status.json` (`review_notes_zh`, `review_file`).

**Important:** Although this evidence exists, the task reverted to `todo` after reassignment.
Gemini2 must verify the evidence is still valid against the current service code and re-run
the acceptance criteria as the new owner. If the evidence is still valid, Gemini2 may use
`progress` to confirm it and proceed to handoff without re-implementing.

---

## 2. EVO-005 Acceptance Criteria (Reference)

From the task definition:

| AC | Description |
|---|---|
| AC-1 | Evidence proves approved rollback command reaches runtime-manager or deployment |
| AC-2 | BFF shows proposed → reviewed → approved → dispatched → executed stages |
| AC-3 | Failure path records blocked reason and retry state |

Proof required: unit tests, contract tests, local service smoke, restart or replay
evidence when worker or runtime behavior changes.

---

## 3. Revised Action Plan for EVO-005 (Gemini2 as Owner)

### 3.1 Fast-Path: Validate Existing Evidence

If Gemini2 can verify the committed evidence is still accurate:

**Step 1 — Gemini2 runs the existing test suite:**

```bash
python3 -m pytest services/evolution/test_evo_005_rollback_followthrough.py -v
# Expect: 20 tests pass
```

**Step 2 — Gemini2 confirms field alignment with current service code:**

```bash
# Check dispatched_at, execution_result, blocked_reason in the BFF model
grep -rn "dispatched_at\|execution_result\|blocked_reason" \
  services/control-plane/bff/ | grep -v ".pyc"
# Expect: fields exist in the BFF response model
```

**Step 3 — Gemini2 progresses and handoffs to Claude:**

```bash
AI_NAME=Gemini2 ./scripts/ai-status.sh progress LOOP-AUTO-EVO-005 \
  "Validated existing evidence: 20 tests pass, fields aligned, review-claude.md still accurate"
AI_NAME=Gemini2 ./scripts/ai-status.sh handoff LOOP-AUTO-EVO-005 Claude \
  "Evidence validated by Gemini2; test suite re-run confirmed; ready for formal approve"
```

**Step 4 — Claude (reviewer) runs formal approve:**

```bash
AI_NAME=Claude \
  REVIEW_FILE=docs/deployment/evidence/loop-auto-evo-005/review-claude.md \
  REVIEW_NOTES_ZH="審查通過：20 tests pass||AC-1 E2E 凍結→rollback-followthrough→RuntimeManagerService.rollback() 驗證完成||AC-2 BFF observation-report 暴露全部五個 stage||AC-3 failure paths 明確 surfaced 阻塞原因" \
  ./scripts/ai-status.sh approve LOOP-AUTO-EVO-005 \
  "Review complete — 20 tests pass, all 3 ACs verified; returning to Gemini2 for finalization"
```

**Step 5 — Gemini2 (owner) finalizes:**

```bash
./scripts/git/task_finalize.sh "LOOP-AUTO-EVO-005"
# Wait for PR to merge into dev
AI_NAME=Gemini2 ./scripts/ai-status.sh done LOOP-AUTO-EVO-005 \
  "Evolution rollback follow-through proven; 20 tests pass; all ACs met; evidence previously committed"
```

### 3.2 Re-implementation Path (if Evidence Is Invalid)

If Gemini2 finds the committed evidence is stale (service code changed, tests fail):

1. Re-implement: update services/evolution and services/runtime-manager as needed
2. Run full acceptance suite to cover AC-1, AC-2, AC-3
3. Commit new evidence to `docs/deployment/evidence/loop-auto-evo-005/`
4. Follow Steps 3–5 from §3.1 above

---

## 4. Updated Dependency Status Snapshot (as of 2026-06-27)

| Task | Title | Status | Note |
|---|---|---|---|
| LOOP-AUTO-SRC-004 | Wire SourceHealth truth into persona panels | **done** | PR #2452 |
| LOOP-AUTO-RT-005 | Produce runtime fleet evidence packet | **done** | done |
| LOOP-AUTO-DEP-004 | Split promotion and deployment BFF truth by stage | **done** | PR #2451 |
| LOOP-AUTO-TEL-005 | Add telemetry incident replay and operator evidence | **done** | done |
| LOOP-AUTO-KNOW-006 | Add consultation workflow executor | **done** | PR #2462 |
| LOOP-AUTO-BFF-001 | Add loop health read model | **done** | PR #2423 |
| LOOP-AUTO-BFF-003 | Label seed snapshot registry scheduled and live truth | **done** | archived |
| **LOOP-AUTO-EVO-005** | **Prove evolution rollback and follow-through** | **`todo` (Gemini2)** | Reassigned; see §1–§3 |

**Net status:** 6 of 7 formal deps done. EVO-005 reassigned to Gemini2 and reset to `todo`.

---

## 5. Updated Surface Gap Registry

### 5.1 Surface Gaps

| Gap ID | Route | Blocking drill | Owned by | Status |
|---|---|---|---|---|
| SG-001 | `GET /api/v1/personas/{id}/source-health` | Drill 1 | SRC-004 | **Likely resolved** — SRC-004 done (PR #2452) |
| SG-002 | `GET /api/v1/source-connectors` (required fields) | Drill 1 | SRC-004 | **Likely resolved** — SRC-004 done; verify fields |
| SG-003 | `GET /api/v1/loops` | Both drills | BFF-001 | **Likely resolved** — BFF-001 done (PR #2423) |
| SG-004 | `GET /api/v1/loops/{loop_id}` | Both drills | BFF-001 | **Likely resolved** — BFF-001 done (PR #2423) |
| SG-005 | `truth_source_label` field | Drill 1 | BFF-003 | **Likely resolved** — BFF-003 done |
| SG-006 | 5-stage deployment split fields | Drill 2 | DEP-004 | **Likely resolved** — DEP-004 done (PR #2451) |
| SG-007 | `dispatched_at`, `execution_result`, `blocked_reason` in EV-02 | Drill 2 | EVO-005 | **Still open** — EVO-005 `todo`/Gemini2; depends on §3 fast-path |

### 5.2 Filter Gaps

| Gap ID | Route | Filter | Status |
|---|---|---|---|
| FG-001 | `GET /api/v1/incidents` | `runtime_id` | Unknown — verify via §6 quick check |
| FG-002 | `GET /api/v1/evolution-decisions` | `incident_id` | Unknown — verify via §6 quick check |

Status is unchanged from FOLLOWUP-4 §3.2. Run the verification commands in §6 before drills.

---

## 6. Pre-Drill Surface Verification Commands (unchanged from FOLLOWUP-4 §3.3)

Run these before starting drills to confirm gap resolution:

```bash
# SG-001: source-health sub-resource
curl -s "$BFF_BASE/api/v1/personas/$PERSONA_ID/source-health" | jq 'type'
# Expect: "array" (not null, not 404)

# SG-002: source-connectors with required fields
curl -s "$BFF_BASE/api/v1/source-connectors" | jq '.[0] | {last_fetch_at, last_push_at, failure_reason, truth_source_label}'
# Expect: all four keys present

# SG-003 / SG-004: loop read model
curl -s "$BFF_BASE/api/v1/loops" | jq '.[0] | {loop_id, current_maturity}'
curl -s "$BFF_BASE/api/v1/loops/source_ingestion" | jq '{loop_id, current_maturity}'
# Expect: loop_id and current_maturity fields present

# SG-005: truth_source_label field
curl -s "$BFF_BASE/api/v1/personas/$PERSONA_ID/source-health" | jq '.[0].truth_source_label'
# Expect: non-null label string

# SG-006: 5-stage deployment split
curl -s "$BFF_BASE/api/v1/runtimes/$RUNTIME_ID/status" | \
  jq '{approval, plan, saga, binding, runtime_fleet}'
# Expect: all five keys present

# SG-007: evolution follow-through fields (only available after EVO-005 done)
curl -s "$BFF_BASE/api/v1/evolution-decisions/$DECISION_ID" | \
  jq '{dispatched_at, execution_result, blocked_reason}'
# Expect: all three keys present

# FG-001: incidents runtime_id filter
curl -s "$BFF_BASE/api/v1/incidents?runtime_id=$RUNTIME_ID" | \
  jq '.meta.filter_applied // "NOT APPLIED"'
# Expect: "runtime_id" or "NOT APPLIED"

# FG-002: evolution-decisions incident_id filter
curl -s "$BFF_BASE/api/v1/evolution-decisions?incident_id=$INCIDENT_ID" | \
  jq '.meta.filter_applied // "NOT APPLIED"'
# Expect: "incident_id" or "NOT APPLIED"
```

---

## 7. Revised Go/No-Go Checklist

### 7.1 Drill 1 (Source-to-Health) — Prerequisites

```
[x] SRC-004, BFF-001, BFF-003 all merged to dev
[ ] Verify SG-001 resolved: source-health returns HTTP 200 (see §6)
[ ] Verify SG-002 resolved: source-connectors has required 4 fields (see §6)
[ ] Verify SG-003 resolved: /api/v1/loops returns loop list (see §6)
[ ] Verify SG-004 resolved: /api/v1/loops/source_ingestion returns detail (see §6)
[ ] Verify SG-005 resolved: truth_source_label non-null (see §6)
[ ] Test environment: dev deployment is current with merged PRs
    Verification: git log --oneline origin/dev | grep -E 'BFF-001|SRC-004|BFF-003'
```

Drill 1 may proceed once SG-001 through SG-005 pass verification and test env is synced.
Drill 1 does **not** require EVO-005 completion.

### 7.2 Drill 2 (Runtime-to-Incident-to-Evolution) — Prerequisites

```
[x] DEP-004, TEL-005, KNOW-006 all merged to dev
[ ] EVO-005 done via §3 sequence (Gemini2 fast-path or re-implementation)
[ ] Verify SG-006 resolved: 5-stage breakdown fields present (see §6)
[ ] Verify SG-007 resolved: EV-02 has dispatched_at, execution_result (see §6)
[ ] Verify FG-001 status: native filter or fallback confirmed (see §6)
[ ] Verify FG-002 status: native filter or fallback confirmed (see §6)
[ ] TEL-005 replay corpus available in test env
    Verification: curl -s "$BFF_BASE/api/v1/telemetry/corpus-status" | jq .
[ ] Test environment: dev deployment is current
    Verification: git log --oneline origin/dev | grep -E 'DEP-004|TEL-005|EVO-005|KNOW-006'
```

### 7.3 Filter Gap Decision (unchanged from FOLLOWUP-3 §2.3)

If FG-001/FG-002 are still pending at drill time:
- **Path A** (native filter available): record resolution task/commit in evidence
- **Path B** (fallback): use FOLLOWUP-2 §4.1 and §4.2; annotate evidence per §4.3;
  BFF-004 reaches `reconciled`, not `proven-live`

---

## 8. Immediate Action Plan for Claude2 (BFF-004 Owner)

### 8.1 Drill 1 (can start immediately)

1. Sync test env: `git log --oneline origin/dev | grep -E 'BFF-001|SRC-004|BFF-003'`
2. Run pre-drill verification §6: SG-001 through SG-005
3. If all pass: run Drill 1 using FOLLOWUP-3 §3.1 evidence template
4. Commit Drill 1 evidence to `docs/deployment/evidence/LOOP-AUTO-BFF-004-drill1-source-health.md`

### 8.2 Drill 2 (requires EVO-005 done)

1. Monitor EVO-005 status: `AI_NAME=Claude2 python3 scripts/ai_status.py show LOOP-AUTO-EVO-005`
2. Once EVO-005 is `done` (after Gemini2 completes §3):
   - Sync test env with latest dev (including EVO-005 PR)
   - Run pre-drill verification §6: SG-006, SG-007, FG-001, FG-002
3. Run Drill 2 using FOLLOWUP-3 §3.2 evidence template
4. Commit Drill 2 evidence to `docs/deployment/evidence/LOOP-AUTO-BFF-004-drill2-runtime-incident-evolution.md`

### 8.3 BFF-004 Closeout

After both drills complete:
1. Select maturity statement — use FOLLOWUP-3 §5 templates
2. Run `task_finalize.sh` and wait for PR merge
3. Run `AI_NAME=Claude2 ./scripts/ai-status.sh done LOOP-AUTO-BFF-004 "<checkpoint message>"`

---

## 9. Updated Risk Register

Changes from FOLLOWUP-4 §7:

| Risk | FOLLOWUP-4 likelihood | Updated likelihood | Note |
|---|---|---|---|
| Replay corpus (TEL-005) not in test env | Low | Low | Unchanged |
| EVO-005 follow-through fields missing | Medium | **Medium** | EVO-005 now `todo`/Gemini2; fast-path may close quickly if evidence valid |
| FG-001/FG-002 rejected with 400 | Medium | Medium | Unchanged; run §6 check |
| `truth_source_label` absent | Low | Low | BFF-003 done |
| Consultation gate blocks drill path | Low | Low | KNOW-006 done |
| Test env not current with merged PRs | Medium | Medium | 8+ PRs merged since FOLLOWUP-3; environment sync required |
| EVO-005 reassignment causes re-implementation | Not listed | **Low–Medium** | Evidence valid if service code unchanged; Gemini2 should run §3.1 fast-path first |
| Drill 2 blocked while Gemini2 implements EVO-005 | New | **Medium** | Drill 1 is unblocked; Drill 2 waits on EVO-005 done; parallelism possible |

---

## 10. Evidence File Paths (reference)

Evidence from dependency tasks (committed in dev):
- SRC-004: `docs/deployment/evidence/loop-auto-src-004/README.md`
- EVO-005: `docs/deployment/evidence/loop-auto-evo-005/review-claude.md` and `README.md`
  (Gemini2 should re-verify and may append evidence as new owner)

Evidence files Claude2 must produce for BFF-004:
- Drill 1: `docs/deployment/evidence/LOOP-AUTO-BFF-004-drill1-source-health.md`
  (use FOLLOWUP-3 §3.1 template)
- Drill 2: `docs/deployment/evidence/LOOP-AUTO-BFF-004-drill2-runtime-incident-evolution.md`
  (use FOLLOWUP-3 §3.2 template)

---

## 11. Cross-Reference Map (updated)

| Topic | Primary source | Update |
|---|---|---|
| Full gap analysis | HANDOFF §3 | Unchanged |
| Filter gap resolution spec | FOLLOWUP-2 §2 | Unchanged |
| Fallback procedures | FOLLOWUP-2 §4 | Unchanged |
| Operator journey steps | HANDOFF §4 | Unchanged |
| Frontend panel readiness | HANDOFF §5 | Dependencies done; panels should be ready once env synced |
| Acceptance checklist | HANDOFF §6 | Unchanged; AC-D1-1 through AC-D2-6 |
| Evidence file templates | FOLLOWUP-3 §3 | Still normative |
| Maturity statement templates | FOLLOWUP-3 §5 | Still normative |
| Pre-drill surface verification | FOLLOWUP-4 §3.3 / **This packet §6** | Unchanged commands |
| Revised go/no-go checklist | **This packet §7** | Replaces FOLLOWUP-4 §4 |
| EVO-005 unblock sequence (old) | FOLLOWUP-4 §2 | Superseded — EVO-005 reassigned to Gemini2; use §3 |
| EVO-005 Gemini2 action plan | **This packet §3** | New; fast-path + re-implementation path |
| EVO-005 reassignment note | **This packet §1** | New |

---

## 12. Sidecar Scope Constraints

This packet is a support artifact only. It:

- Does **not** modify any L1 canonical policy file
- Does **not** modify `ai-status.json`, `current-work.md`, or any loop registry
- Does **not** implement any BFF route, filter handler, or evidence collection
- Does **not** change the parent task's acceptance criteria
- Documents the EVO-005 reassignment and provides the revised action plan
- Must be absorbed into the parent task's final evidence packet at
  LOOP-AUTO-BFF-004 closeout alongside all prior sidecar packets

---

## 13. Change Log

| Date | Author | Change |
|---|---|---|
| 2026-06-27 | Claude | Initial FOLLOWUP-5 packet: EVO-005 reassignment from Claude2/blocked to Gemini2/todo; revised EVO-005 action plan (fast-path + re-implementation); updated dependency table; updated risk register; revised go/no-go checklist; immediate action plan for Claude2 |
