# BFF Drill Readiness — Live State Reconciliation: LOOP-AUTO-BFF-004 — FOLLOWUP-6

**Sidecar kind:** bff_handoff_packet (live state reconciliation)
**Parent task:** LOOP-AUTO-BFF-004
**Sidecar task:** LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-6
**Prepared by:** Claude
**Date:** 2026-06-27
**Reviewer:** Claude2

---

## Purpose

FOLLOWUP-5 documented a Gemini2 reassignment of EVO-005 (from Claude2/blocked to
Gemini2/todo). Live state audit (`python3 scripts/ai_status.py show LOOP-AUTO-EVO-005`)
performed during this packet's preparation shows that reassignment did **not** persist in
the live supervisor state. EVO-005 is still `blocked` with Claude2 as owner and Claude as
reviewer, waiting for the formal `approve` command.

This packet:

1. Documents the discrepancy between FOLLOWUP-5 and current live state
2. Confirms EVO-005 is still in the FOLLOWUP-4 §2 scenario
3. Provides a definitive current state snapshot across all BFF-004 dependencies
4. Provides the correct immediate action plan (FOLLOWUP-4 §2 unblock, not FOLLOWUP-5 §3 Gemini2 path)

Prior packets remain normative for their respective topics:
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF.md` — full gap analysis and operator journey
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` — filter gap resolution spec
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md` — consolidated pre-drill packet (primary drill reference)
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md` — EVO-005 unblock sequence (§2 still applies)
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md` — EVO-005 Gemini2 plan (§1–§3 superseded by this packet; §4–§13 remain valid except where noted)

This packet does **not** modify any L1 canonical policy, `ai-status.json`, the
loop registry, or any BFF implementation file.

---

## 1. Live State Audit — EVO-005

### 1.1 FOLLOWUP-5 Description

FOLLOWUP-5 (§1.2) stated that EVO-005 was reassigned:

| Field | FOLLOWUP-5 value |
|---|---|
| Status | `todo` |
| Owner | Gemini2 |
| Reviewer | Claude |
| Waiting for | (none — available for fresh dispatch) |

### 1.2 Actual Live State (as of 2026-06-27T~20:00Z)

`python3 scripts/ai_status.py show LOOP-AUTO-EVO-005` returns:

| Field | Live value |
|---|---|
| Status | `blocked` |
| Owner | Claude2 |
| Reviewer | Claude |
| Waiting for | Claude (formal `approve` command) |
| Next | "PR 2475 merged and CI green; review doc committed; formal `ai-status.sh approve` was never run — needs Claude to run approve command before Claude2 can run done" |

### 1.3 Discrepancy Resolution

The Gemini2 reassignment described in FOLLOWUP-5 did not take effect in the live
supervisor state. Either:

- (a) The reassignment was proposed but the `assign` command was never successfully
  executed against the live supervisor, or
- (b) The reassignment was executed but subsequently reverted

Regardless of cause, **the FOLLOWUP-4 §2 unblock sequence is the correct current
path**, not FOLLOWUP-5 §3 (Gemini2 fast-path or re-implementation). FOLLOWUP-5 §3
is superseded by this reconciliation.

---

## 2. Authoritative Dependency Status Snapshot (2026-06-27 live)

Status verified via `python3 scripts/ai_status.py show <task>`:

| Task | Title | Live status | Evidence |
|---|---|---|---|
| LOOP-AUTO-SRC-004 | Wire SourceHealth truth into persona panels | **done** (not found in active = archived) | PR #2452; `docs/deployment/evidence/loop-auto-src-004/README.md` |
| LOOP-AUTO-RT-005 | Produce runtime fleet evidence packet | **done** (not found in active = archived) | Done per FOLLOWUP-5 §4 |
| LOOP-AUTO-DEP-004 | Split promotion and deployment BFF truth by stage | **done** (not found in active = archived) | PR #2451 |
| LOOP-AUTO-TEL-005 | Add telemetry incident replay and operator evidence | **done** (not found in active = archived) | Done per FOLLOWUP-5 §4 |
| LOOP-AUTO-KNOW-006 | Add consultation workflow executor | **done** (not found in active = archived) | PR #2462 |
| LOOP-AUTO-BFF-001 | Add loop health read model | **done** (archived 2026-06-27T13:57:52Z confirmed) | Archive record shows `terminal_status: done` |
| LOOP-AUTO-BFF-003 | Label seed snapshot registry scheduled and live truth | **done** (not found in active = archived) | Archived per FOLLOWUP-4 §1 |
| **LOOP-AUTO-EVO-005** | **Prove evolution rollback and follow-through** | **`blocked`** (live confirmed) | Owner Claude2; waiting for Claude approve (see §3) |
| **LOOP-AUTO-BFF-004** | **Run cross-loop operator drills (parent)** | **`todo`** (live confirmed) | Waiting for EVO-005 unblock |

**Net status:** 7 of 8 formal deps done. EVO-005 blocked in the FOLLOWUP-4 state.

---

## 3. Correct Unblock Path: FOLLOWUP-4 §2 (not FOLLOWUP-5 §3)

FOLLOWUP-5 §3 (Gemini2 fast-path and re-implementation) is superseded. Use
FOLLOWUP-4 §2 exactly as written. The sequence is reproduced here for convenience:

### Step 1 — Claude2 (owner): Re-handoff EVO-005 to Claude

EVO-005 is currently `blocked`. Move it back to `review` by re-running handoff:

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh handoff LOOP-AUTO-EVO-005 Claude \
  "Re-handoff to Claude for formal approve — PR 2475 merged, review file at \
   docs/deployment/evidence/loop-auto-evo-005/review-claude.md already shows APPROVED"
```

### Step 2 — Claude (reviewer): Run formal approve

The review evidence is already committed. Claude must run the approve command to
move EVO-005 to `review_approved`:

```bash
AI_NAME=Claude \
  REVIEW_FILE=docs/deployment/evidence/loop-auto-evo-005/review-claude.md \
  REVIEW_NOTES_ZH="審查通過：20 tests pass||AC-1 E2E 凍結→rollback-followthrough→RuntimeManagerService.rollback() 驗證完成||AC-2 BFF observation-report 暴露全部五個 stage||AC-3 failure paths 明確 surfaced 阻塞原因" \
  ./scripts/ai-status.sh approve LOOP-AUTO-EVO-005 \
  "Review complete — 20 tests pass, all 3 ACs verified; returning to Claude2 for finalization"
```

### Step 3 — Claude2 (owner): Finalize EVO-005

After approve, Claude2 runs done:

```bash
./scripts/git/task_finalize.sh "LOOP-AUTO-EVO-005"
# Wait for PR to merge into dev
AI_NAME=Claude2 ./scripts/ai-status.sh done LOOP-AUTO-EVO-005 \
  "Evolution rollback follow-through proven; PR 2475 merged, review approved, evidence committed"
```

After Step 3, all BFF-004 dependencies are `done` and Claude2 may begin drills.

---

## 4. EVO-005 Existing Evidence (reference for Step 2/3)

Review evidence already committed in the repo:

| Artifact | Path | Status |
|---|---|---|
| Test suite | `services/evolution/test_evo_005_rollback_followthrough.py` | Committed; 20 tests pass |
| Evidence README | `docs/deployment/evidence/loop-auto-evo-005/README.md` | Committed |
| Review document | `docs/deployment/evidence/loop-auto-evo-005/review-claude.md` | Committed; verdict APPROVED |

Review notes already in live `ai-status.json` (stored from prior blocked-state fields):
- `review_notes_zh`: 審查通過：20 tests pass, AC-1/AC-2/AC-3 verified
- `review_file`: `docs/deployment/evidence/loop-auto-evo-005/review-claude.md`

No new evidence needs to be generated — the Step 2 `approve` command formalizes
what the review document already records.

---

## 5. Surface Gap and Filter Gap Registry (current)

No changes from FOLLOWUP-5 §5. The registry is:

### 5.1 Surface Gaps

| Gap ID | Route | Blocking drill | Status |
|---|---|---|---|
| SG-001 | `GET /api/v1/personas/{id}/source-health` | Drill 1 | **Likely resolved** — SRC-004 done |
| SG-002 | `GET /api/v1/source-connectors` (required fields) | Drill 1 | **Likely resolved** — SRC-004 done |
| SG-003 | `GET /api/v1/loops` | Both drills | **Likely resolved** — BFF-001 done |
| SG-004 | `GET /api/v1/loops/{loop_id}` | Both drills | **Likely resolved** — BFF-001 done |
| SG-005 | `truth_source_label` field | Drill 1 | **Likely resolved** — BFF-003 done |
| SG-006 | 5-stage deployment split fields | Drill 2 | **Likely resolved** — DEP-004 done |
| SG-007 | `dispatched_at`, `execution_result`, `blocked_reason` | Drill 2 | **Still open** — EVO-005 blocked; use §3 path |

### 5.2 Filter Gaps

| Gap ID | Route | Filter | Status |
|---|---|---|---|
| FG-001 | `GET /api/v1/incidents` | `runtime_id` | Unknown — verify per §6 |
| FG-002 | `GET /api/v1/evolution-decisions` | `incident_id` | Unknown — verify per §6 |

---

## 6. Pre-Drill Surface Verification Commands (unchanged from FOLLOWUP-5 §6)

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

### 7.1 Drill 1 (Source-to-Health) — Unblocked

```
[x] SRC-004, BFF-001, BFF-003 done (live confirmed)
[ ] Verify SG-001 resolved: source-health returns HTTP 200 (see §6)
[ ] Verify SG-002 resolved: source-connectors has required 4 fields (see §6)
[ ] Verify SG-003 resolved: /api/v1/loops returns loop list (see §6)
[ ] Verify SG-004 resolved: /api/v1/loops/source_ingestion returns detail (see §6)
[ ] Verify SG-005 resolved: truth_source_label non-null (see §6)
[ ] Test environment: dev deployment is current with merged PRs
    Verification: git log --oneline origin/dev | grep -E 'BFF-001|SRC-004|BFF-003'
```

Drill 1 does **not** require EVO-005 completion.

### 7.2 Drill 2 (Runtime-to-Incident-to-Evolution) — Blocked on EVO-005

```
[x] DEP-004, TEL-005, KNOW-006 done (live confirmed)
[ ] EVO-005 unblocked and done — use §3 sequence (Step 1 by Claude2, Step 2 by Claude,
    Step 3 by Claude2)
[ ] Verify SG-006 resolved: 5-stage breakdown fields present (see §6)
[ ] Verify SG-007 resolved: EV-02 has dispatched_at, execution_result (see §6)
[ ] Verify FG-001 status: native filter or fallback confirmed (see §6)
[ ] Verify FG-002 status: native filter or fallback confirmed (see §6)
[ ] TEL-005 replay corpus available in test env
    Verification: curl -s "$BFF_BASE/api/v1/telemetry/corpus-status" | jq .
[ ] Test environment: dev deployment is current
    Verification: git log --oneline origin/dev | grep -E 'DEP-004|TEL-005|EVO-005|KNOW-006'
```

### 7.3 Filter Gap Decision (unchanged from prior packets)

If FG-001/FG-002 are still pending at drill time:
- **Path A** (native filter available): record resolution task/commit in evidence
- **Path B** (fallback): use FOLLOWUP-2 §4.1 and §4.2; annotate evidence per §4.3;
  BFF-004 reaches `reconciled`, not `proven-live`

---

## 8. Immediate Action Plan

### 8.1 For Claude2

**Immediate (EVO-005 Step 1):**
```bash
AI_NAME=Claude2 ./scripts/ai-status.sh handoff LOOP-AUTO-EVO-005 Claude \
  "Re-handoff to Claude for formal approve — PR 2475 merged, review-claude.md shows APPROVED"
```

**Drill 1 (can start immediately — does not require EVO-005):**
1. Sync test env: `git log --oneline origin/dev | grep -E 'BFF-001|SRC-004|BFF-003'`
2. Run pre-drill verification §6: SG-001 through SG-005
3. If all pass: run Drill 1 using FOLLOWUP-3 §3.1 evidence template
4. Commit Drill 1 evidence to `docs/deployment/evidence/LOOP-AUTO-BFF-004-drill1-source-health.md`

**Drill 2 (requires EVO-005 done via §3 sequence):**
1. Monitor EVO-005: `python3 scripts/ai_status.py show LOOP-AUTO-EVO-005`
2. Once EVO-005 is `done`: sync test env with latest dev
3. Run pre-drill verification §6: SG-006, SG-007, FG-001, FG-002
4. Run Drill 2 using FOLLOWUP-3 §3.2 evidence template
5. Commit Drill 2 evidence to `docs/deployment/evidence/LOOP-AUTO-BFF-004-drill2-runtime-incident-evolution.md`

**BFF-004 Closeout (after both drills):**
1. Select maturity statement — use FOLLOWUP-3 §5 templates
2. Run `task_finalize.sh` and wait for PR merge
3. Run `AI_NAME=Claude2 ./scripts/ai-status.sh done LOOP-AUTO-BFF-004 "<checkpoint>"`

### 8.2 For Claude (EVO-005 Step 2)

Once Claude2 re-handoffs EVO-005, Claude runs the approve:
```bash
AI_NAME=Claude \
  REVIEW_FILE=docs/deployment/evidence/loop-auto-evo-005/review-claude.md \
  REVIEW_NOTES_ZH="審查通過：20 tests pass||AC-1 E2E 凍結→rollback-followthrough→RuntimeManagerService.rollback() 驗證完成||AC-2 BFF observation-report 暴露全部五個 stage||AC-3 failure paths 明確 surfaced 阻塞原因" \
  ./scripts/ai-status.sh approve LOOP-AUTO-EVO-005 \
  "Review complete — 20 tests pass, all 3 ACs verified; returning to Claude2 for finalization"
```

---

## 9. Updated Risk Register

Changes from FOLLOWUP-5 §9:

| Risk | FOLLOWUP-5 likelihood | Updated likelihood | Note |
|---|---|---|---|
| EVO-005 reassignment to Gemini2 causes fresh implementation | Low–Medium | **Not applicable** | Reassignment didn't persist; back to FOLLOWUP-4 §2 path |
| EVO-005 approve step not executed | Not listed | **High** | Active risk; requires Claude2 re-handoff then Claude approve |
| Drill 2 blocked while EVO-005 resolves | Medium | **Medium** | Drill 1 unblocked; Drill 2 waits on §3 sequence |
| Replay corpus (TEL-005) not in test env | Low | Low | Unchanged |
| FG-001/FG-002 rejected with 400 | Medium | Medium | Unchanged; run §6 check |
| `truth_source_label` absent | Low | Low | BFF-003 done |
| Test env not current with merged PRs | Medium | Medium | Sync before drills |

**New risk:** If neither Claude2 nor Claude acts on the §3 sequence promptly, BFF-004
will remain blocked indefinitely. Chair-review should flag this if BFF-004 is still
`todo` after this packet is reviewed.

---

## 10. Evidence File Paths (reference)

Evidence from dependency tasks (committed in dev):
- SRC-004: `docs/deployment/evidence/loop-auto-src-004/README.md`
- EVO-005: `docs/deployment/evidence/loop-auto-evo-005/review-claude.md` and `README.md`

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
| Frontend panel readiness | HANDOFF §5 | Dependencies done; panels ready once env synced |
| Acceptance checklist | HANDOFF §6 | Unchanged; AC-D1-1 through AC-D2-6 |
| Evidence file templates | FOLLOWUP-3 §3 | Still normative |
| Maturity statement templates | FOLLOWUP-3 §5 | Still normative |
| Pre-drill surface verification | FOLLOWUP-5 §6 / **This packet §6** | Unchanged commands |
| EVO-005 unblock sequence | FOLLOWUP-4 §2 / **This packet §3** | FOLLOWUP-4 §2 still applies; FOLLOWUP-5 §3 superseded |
| EVO-005 Gemini2 plan | FOLLOWUP-5 §3 | **Superseded** — EVO-005 not reassigned; use §3 of this packet |
| Go/no-go checklist | **This packet §7** | Replaces FOLLOWUP-5 §7; same items, EVO-005 path corrected |
| Immediate action plan | **This packet §8** | Replaces FOLLOWUP-5 §8; corrects Claude2/Claude roles |

---

## 12. Sidecar Scope Constraints

This packet is a support artifact only. It:

- Does **not** modify any L1 canonical policy file
- Does **not** modify `ai-status.json`, `current-work.md`, or any loop registry
- Does **not** implement any BFF route, filter handler, or evidence collection
- Does **not** change the parent task's acceptance criteria
- Documents the live state reconciliation (EVO-005 not reassigned to Gemini2)
  and provides the correct current action plan for Claude2 and Claude
- Must be absorbed into the parent task's final evidence packet at
  LOOP-AUTO-BFF-004 closeout alongside all prior sidecar packets

---

## 13. Change Log

| Date | Author | Change |
|---|---|---|
| 2026-06-27 | Claude | Initial FOLLOWUP-6 packet: live state audit confirms EVO-005 still blocked (Claude2/blocked, not Gemini2/todo); reconciles FOLLOWUP-5 discrepancy; confirms FOLLOWUP-4 §2 unblock sequence applies; updates dep status table (BFF-001 archive confirmed); provides corrected go/no-go checklist and action plan for Claude2 and Claude |
