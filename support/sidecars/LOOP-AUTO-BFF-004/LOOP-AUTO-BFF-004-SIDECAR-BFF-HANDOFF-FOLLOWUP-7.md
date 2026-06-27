# BFF Drill Readiness — Worktree Mirror Discrepancy and Stall Escalation: LOOP-AUTO-BFF-004 — FOLLOWUP-7

**Sidecar kind:** bff_handoff_packet (live state re-audit + stall escalation)
**Parent task:** LOOP-AUTO-BFF-004
**Sidecar task:** LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-7
**Prepared by:** Claude
**Date:** 2026-06-27
**Reviewer:** Claude2

---

## Purpose

FOLLOWUP-6 confirmed that EVO-005 was still `blocked`/Claude2 (contradicting FOLLOWUP-5's
`todo`/Gemini2 description). FOLLOWUP-7 re-audits the live supervisor state to confirm
FOLLOWUP-6's accuracy and identifies the root cause of the recurring discrepancy: the
**worktree's `ai-status.json` is a stale point-in-time mirror** that disagrees with the
live supervisor on the state of EVO-005 and all BFF-004 dependencies.

This packet:

1. Documents the worktree mirror vs. live supervisor discrepancy (root cause of prior confusion)
2. Confirms FOLLOWUP-6 was accurate (EVO-005 still blocked/Claude2 as of 2026-06-27T18:23:52Z)
3. Confirms all other deps remain archived/done
4. Reiterates the correct action plan (FOLLOWUP-4 §2 / FOLLOWUP-6 §3)
5. Escalates to chair-review: the EVO-005 unblock sequence has been documented across four
   consecutive packets (FOLLOWUP-4 through FOLLOWUP-7) without execution

Prior packets remain normative for their respective topics:
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF.md` — full gap analysis and operator journey
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` — filter gap resolution spec
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md` — consolidated pre-drill packet (primary drill reference)
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md` — EVO-005 unblock sequence (§2 still applies)
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md` — superseded for §1–§3 (EVO-005 state); §4–§13 valid
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md` — **confirmed accurate**; EVO-005 blocked/Claude2

This packet does **not** modify any L1 canonical policy, `ai-status.json`, the
loop registry, or any BFF implementation file.

---

## 1. Root Cause of Prior Discrepancy: Worktree Mirror vs. Live Supervisor

### 1.1 Observation

Across FOLLOWUP-5 and FOLLOWUP-6, the sidecar preparers saw conflicting states for
EVO-005. FOLLOWUP-7 has identified the root cause:

The worktree's local `ai-status.json` is a **stale mirror** created when tasks were
materialized on 2026-06-27 at approximately 06:44:58Z. It reflects the initial
materialization state — not the live supervisor state — and diverges from it when
the supervisor has applied subsequent status updates.

### 1.2 Worktree Mirror State (DO NOT USE for task decisions)

Reading `ai-status.json` directly from the worktree shows:

| Task | Worktree mirror status | Worktree mirror owner |
|---|---|---|
| LOOP-AUTO-EVO-005 | `todo` | Gemini2 |
| LOOP-AUTO-SRC-004 | `todo` | Codex2 |
| LOOP-AUTO-RT-005 | `todo` | Codex2 |
| LOOP-AUTO-DEP-004 | `todo` | Codex2 |
| LOOP-AUTO-TEL-005 | `todo` | Gemini2 |
| LOOP-AUTO-KNOW-006 | `todo` | Claude |
| LOOP-AUTO-BFF-001 | `todo` | Codex |
| LOOP-AUTO-BFF-003 | `todo` | Codex2 |

This data is materially wrong for all eight tasks.

### 1.3 Live Supervisor State (authoritative — use `python3 scripts/ai_status.py show`)

Verified via `AI_NAME=Claude python3 scripts/ai_status.py show <task>`:

| Task | Live status | Live owner | Source |
|---|---|---|---|
| LOOP-AUTO-EVO-005 | `blocked` | Claude2 | `active` |
| LOOP-AUTO-SRC-004 | (not applicable) | — | `archive` |
| LOOP-AUTO-RT-005 | (not applicable) | — | `archive` |
| LOOP-AUTO-DEP-004 | (not applicable) | — | `archive` |
| LOOP-AUTO-TEL-005 | (not applicable) | — | `archive` |
| LOOP-AUTO-KNOW-006 | (not applicable) | — | `archive` |
| LOOP-AUTO-BFF-001 | (not applicable) | — | `archive` |
| LOOP-AUTO-BFF-003 | (not applicable) | — | `archive` |

`archive` source = task was `done` and archived by the supervisor. Seven of eight
BFF-004 dependencies are done. EVO-005 is the sole remaining blocker.

### 1.4 How to Query the Live State (Mandatory Protocol)

Future sidecar preparers and auto workers must use the live supervisor query, not
the worktree file:

```bash
AI_NAME=Claude python3 scripts/ai_status.py show LOOP-AUTO-EVO-005
AI_NAME=Claude python3 scripts/ai_status.py show LOOP-AUTO-BFF-004
```

Do not read `ai-status.json` directly and treat it as live state. The worktree copy
is a mirror for display only.

---

## 2. Authoritative Dependency Status Snapshot (2026-06-27 live, FOLLOWUP-7 audit)

| Task | Title | Live supervisor status | Notes |
|---|---|---|---|
| LOOP-AUTO-SRC-004 | Wire SourceHealth truth into persona panels | **done** (archived) | PR #2452 |
| LOOP-AUTO-RT-005 | Produce runtime fleet evidence packet | **done** (archived) | Done per FOLLOWUP-5 §4 |
| LOOP-AUTO-DEP-004 | Split promotion and deployment BFF truth by stage | **done** (archived) | PR #2451 |
| LOOP-AUTO-TEL-005 | Add telemetry incident replay and operator evidence | **done** (archived) | Done per FOLLOWUP-5 §4 |
| LOOP-AUTO-KNOW-006 | Add consultation workflow executor | **done** (archived) | PR #2462 |
| LOOP-AUTO-BFF-001 | Add loop health read model | **done** (archived) | PR #2423 |
| LOOP-AUTO-BFF-003 | Label seed snapshot registry scheduled and live truth | **done** (archived) | Archived per FOLLOWUP-4 §1 |
| **LOOP-AUTO-EVO-005** | **Prove evolution rollback and follow-through** | **`blocked`** (active) | Owner Claude2; waiting_for Claude; last_update 2026-06-27T18:23:52Z |
| **LOOP-AUTO-BFF-004** | **Run cross-loop operator drills (parent)** | **`todo`** (active) | Waiting for EVO-005 done |

**Net status:** 7 of 8 deps done (archived). EVO-005 still blocked in the FOLLOWUP-4/6 state.
**No change from FOLLOWUP-6.**

---

## 3. EVO-005 Current Live State (confirmed)

From `AI_NAME=Claude python3 scripts/ai_status.py show LOOP-AUTO-EVO-005`:

```
status:        blocked
owner:         Claude2
reviewer:      Claude
waiting_for:   Claude
last_update:   2026-06-27T18:23:52Z
review_file:   docs/deployment/evidence/loop-auto-evo-005/review-claude.md
review_notes_zh:
  - 審查通過：20 tests pass
  - AC-1 E2E 凍結→rollback-followthrough→RuntimeManagerService.rollback() 驗證完成
  - AC-2 BFF observation-report 暴露全部五個 stage
  - AC-3 failure paths 明確 surfaced 阻塞原因
next:          "PR 2475 merged and CI green; review doc committed in 4e43453b shows
               Claude APPROVED; formal ai-status.sh approve transition was never run
               — needs Claude to run approve command before Claude2 can run done"
```

**The state is identical to what FOLLOWUP-6 §1.2 documented.** No progress on the
unblock sequence between FOLLOWUP-6 (audit at ~18:23 UTC) and FOLLOWUP-7.

---

## 4. Correct Unblock Path (unchanged from FOLLOWUP-4 §2 / FOLLOWUP-6 §3)

The FOLLOWUP-4 §2 / FOLLOWUP-6 §3 sequence is still the only correct path.
Reproduced for completeness:

### Step 1 — Claude2 (owner): Re-handoff EVO-005 to Claude

Move EVO-005 from `blocked` back to `review` by running:

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh handoff LOOP-AUTO-EVO-005 Claude \
  "Re-handoff to Claude for formal approve — PR 2475 merged, review-claude.md shows APPROVED"
```

### Step 2 — Claude (reviewer): Run formal approve

The review evidence is already committed and review notes are already in the live
supervisor state. Claude runs:

```bash
AI_NAME=Claude \
  REVIEW_FILE=docs/deployment/evidence/loop-auto-evo-005/review-claude.md \
  REVIEW_NOTES_ZH="審查通過：20 tests pass||AC-1 E2E 凍結→rollback-followthrough→RuntimeManagerService.rollback() 驗證完成||AC-2 BFF observation-report 暴露全部五個 stage||AC-3 failure paths 明確 surfaced 阻塞原因" \
  ./scripts/ai-status.sh approve LOOP-AUTO-EVO-005 \
  "Review complete — 20 tests pass, all 3 ACs verified; returning to Claude2 for finalization"
```

### Step 3 — Claude2 (owner): Finalize EVO-005

```bash
./scripts/git/task_finalize.sh "LOOP-AUTO-EVO-005"
# Wait for PR to merge into dev
AI_NAME=Claude2 ./scripts/ai-status.sh done LOOP-AUTO-EVO-005 \
  "Evolution rollback follow-through proven; PR 2475 merged, review approved, evidence committed"
```

After Step 3, all BFF-004 dependencies are `done` and Claude2 may begin drills.

---

## 5. Stall Escalation — Chair-Review Required

The EVO-005 unblock sequence (FOLLOWUP-4 §2) was first documented in the
FOLLOWUP-4 packet. It has now appeared in four consecutive packets without execution:

| Packet | Description | EVO-005 state documented |
|---|---|---|
| FOLLOWUP-4 | First introduced unblock sequence | `blocked`/Claude2 — unblock sequence §2 written |
| FOLLOWUP-5 | Described Gemini2 reassignment (worktree mirror artifact) | `todo`/Gemini2 (incorrect per live state) |
| FOLLOWUP-6 | Confirmed FOLLOWUP-4 still applies (live state audit) | `blocked`/Claude2 — same as FOLLOWUP-4 |
| **FOLLOWUP-7** | **Confirms FOLLOWUP-6; identifies mirror root cause** | `blocked`/Claude2 — **no change** |

The unblock requires exactly two commands:
1. `Claude2 handoff LOOP-AUTO-EVO-005 Claude` — 1 command
2. `Claude approve LOOP-AUTO-EVO-005` — 1 command

Neither has been executed. The evidence is already committed. No new implementation
is needed.

**Chair-review action requested:** Flag EVO-005 as actively stalled. If Claude2 is
unavailable or consistently failing on this command, the supervisor should:

- Attempt forced re-dispatch of EVO-005 to Claude2 for the single handoff command
- Or reassign EVO-005 to an available owner and notify Claude for the approve step
- Do not produce additional FOLLOWUP sidecar packets without first attempting to
  execute the unblock

---

## 6. Surface Gap and Filter Gap Registry (unchanged from FOLLOWUP-6 §5)

### 6.1 Surface Gaps

| Gap ID | Route | Blocking drill | Status |
|---|---|---|---|
| SG-001 | `GET /api/v1/personas/{id}/source-health` | Drill 1 | **Likely resolved** — SRC-004 done |
| SG-002 | `GET /api/v1/source-connectors` (required fields) | Drill 1 | **Likely resolved** — SRC-004 done |
| SG-003 | `GET /api/v1/loops` | Both drills | **Likely resolved** — BFF-001 done |
| SG-004 | `GET /api/v1/loops/{loop_id}` | Both drills | **Likely resolved** — BFF-001 done |
| SG-005 | `truth_source_label` field | Drill 1 | **Likely resolved** — BFF-003 done |
| SG-006 | 5-stage deployment split fields | Drill 2 | **Likely resolved** — DEP-004 done |
| SG-007 | `dispatched_at`, `execution_result`, `blocked_reason` | Drill 2 | **Still open** — EVO-005 blocked; use §4 path |

### 6.2 Filter Gaps

| Gap ID | Route | Filter | Status |
|---|---|---|---|
| FG-001 | `GET /api/v1/incidents` | `runtime_id` | Unknown — verify per §7 |
| FG-002 | `GET /api/v1/evolution-decisions` | `incident_id` | Unknown — verify per §7 |

---

## 7. Pre-Drill Surface Verification Commands (unchanged from FOLLOWUP-6 §6)

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

## 8. Go/No-Go Checklist (current)

### 8.1 Drill 1 (Source-to-Health) — Unblocked

Drill 1 does **not** require EVO-005 completion.

```
[x] SRC-004, BFF-001, BFF-003 done (archived, live confirmed)
[ ] Verify SG-001 resolved: source-health returns HTTP 200 (see §7)
[ ] Verify SG-002 resolved: source-connectors has required 4 fields (see §7)
[ ] Verify SG-003 resolved: /api/v1/loops returns loop list (see §7)
[ ] Verify SG-004 resolved: /api/v1/loops/source_ingestion returns detail (see §7)
[ ] Verify SG-005 resolved: truth_source_label non-null (see §7)
[ ] Test environment: dev deployment is current with merged PRs
    Verification: git log --oneline origin/dev | grep -E 'BFF-001|SRC-004|BFF-003'
```

### 8.2 Drill 2 (Runtime-to-Incident-to-Evolution) — Blocked on EVO-005

```
[x] DEP-004, TEL-005, KNOW-006 done (archived, live confirmed)
[ ] EVO-005 unblocked and done — use §4 sequence (Step 1: Claude2, Step 2: Claude, Step 3: Claude2)
[ ] Verify SG-006 resolved: 5-stage breakdown fields present (see §7)
[ ] Verify SG-007 resolved: EV-02 has dispatched_at, execution_result (see §7)
[ ] Verify FG-001 status: native filter or fallback confirmed (see §7)
[ ] Verify FG-002 status: native filter or fallback confirmed (see §7)
[ ] TEL-005 replay corpus available in test env
    Verification: curl -s "$BFF_BASE/api/v1/telemetry/corpus-status" | jq .
[ ] Test environment: dev deployment is current
    Verification: git log --oneline origin/dev | grep -E 'DEP-004|TEL-005|EVO-005|KNOW-006'
```

### 8.3 Filter Gap Decision (unchanged from prior packets)

If FG-001/FG-002 are still pending at drill time:
- **Path A** (native filter available): record resolution task/commit in evidence
- **Path B** (fallback): use FOLLOWUP-2 §4.1 and §4.2; annotate evidence per §4.3;
  BFF-004 reaches `reconciled`, not `proven-live`

---

## 9. Immediate Action Plan

### 9.1 Highest Priority: Execute the EVO-005 Unblock (Claude2 then Claude)

**Step 1 — Claude2 (1 command, immediately executable):**

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh handoff LOOP-AUTO-EVO-005 Claude \
  "Re-handoff to Claude for formal approve — PR 2475 merged, review-claude.md shows APPROVED"
```

**Step 2 — Claude (1 command, immediately executable after Step 1):**

```bash
AI_NAME=Claude \
  REVIEW_FILE=docs/deployment/evidence/loop-auto-evo-005/review-claude.md \
  REVIEW_NOTES_ZH="審查通過：20 tests pass||AC-1 E2E 凍結→rollback-followthrough→RuntimeManagerService.rollback() 驗證完成||AC-2 BFF observation-report 暴露全部五個 stage||AC-3 failure paths 明確 surfaced 阻塞原因" \
  ./scripts/ai-status.sh approve LOOP-AUTO-EVO-005 \
  "Review complete — 20 tests pass, all 3 ACs verified; returning to Claude2 for finalization"
```

**Step 3 — Claude2 (after approve):**

```bash
./scripts/git/task_finalize.sh "LOOP-AUTO-EVO-005"
AI_NAME=Claude2 ./scripts/ai-status.sh done LOOP-AUTO-EVO-005 \
  "Evolution rollback follow-through proven; PR 2475 merged; review approved; evidence committed"
```

### 9.2 Drill 1 (Claude2 can start immediately — does not require EVO-005)

1. Sync test env: `git log --oneline origin/dev | grep -E 'BFF-001|SRC-004|BFF-003'`
2. Run pre-drill verification §7: SG-001 through SG-005
3. If all pass: run Drill 1 using FOLLOWUP-3 §3.1 evidence template
4. Commit Drill 1 evidence to `docs/deployment/evidence/LOOP-AUTO-BFF-004-drill1-source-health.md`

### 9.3 Drill 2 (Claude2 after EVO-005 done)

1. Confirm EVO-005 done: `AI_NAME=Claude2 python3 scripts/ai_status.py show LOOP-AUTO-EVO-005`
2. Sync test env: `git log --oneline origin/dev | grep -E 'DEP-004|TEL-005|EVO-005|KNOW-006'`
3. Run pre-drill verification §7: SG-006, SG-007, FG-001, FG-002
4. Run Drill 2 using FOLLOWUP-3 §3.2 evidence template
5. Commit evidence to `docs/deployment/evidence/LOOP-AUTO-BFF-004-drill2-runtime-incident-evolution.md`

### 9.4 BFF-004 Closeout (Claude2 after both drills)

1. Select maturity statement — use FOLLOWUP-3 §5 templates
2. Run `task_finalize.sh` and wait for PR merge
3. Run `AI_NAME=Claude2 ./scripts/ai-status.sh done LOOP-AUTO-BFF-004 "<checkpoint>"`

---

## 10. Risk Register (updated)

Changes from FOLLOWUP-6 §9:

| Risk | FOLLOWUP-6 likelihood | Updated likelihood | Note |
|---|---|---|---|
| EVO-005 approve step not executed | **High** | **Critical** | Four consecutive packets; unblock is 2 commands; still not done |
| Worktree mirror causes future confusion | Not listed | **High → Mitigated** | Root cause identified (§1); mandatory protocol documented (§1.4) |
| Drill 2 blocked while EVO-005 resolves | Medium | Medium | Drill 1 unblocked; Drill 2 waits on §4 sequence |
| Replay corpus (TEL-005) not in test env | Low | Low | Unchanged |
| FG-001/FG-002 rejected with 400 | Medium | Medium | Unchanged; run §7 check |
| `truth_source_label` absent | Low | Low | BFF-003 done |
| Test env not current with merged PRs | Medium | Medium | Sync before drills |

---

## 11. Evidence File Paths (reference)

Evidence already committed in dev:
- SRC-004: `docs/deployment/evidence/loop-auto-src-004/README.md`
- EVO-005: `docs/deployment/evidence/loop-auto-evo-005/review-claude.md` and `README.md`
  (review notes already in live supervisor `review_notes_zh` and `review_file` fields)

Evidence Claude2 must produce for BFF-004 drills:
- Drill 1: `docs/deployment/evidence/LOOP-AUTO-BFF-004-drill1-source-health.md`
  (use FOLLOWUP-3 §3.1 template)
- Drill 2: `docs/deployment/evidence/LOOP-AUTO-BFF-004-drill2-runtime-incident-evolution.md`
  (use FOLLOWUP-3 §3.2 template)

---

## 12. Cross-Reference Map (updated)

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
| Pre-drill surface verification | FOLLOWUP-6 §6 / **This packet §7** | Unchanged commands |
| EVO-005 unblock sequence | FOLLOWUP-4 §2 / FOLLOWUP-6 §3 / **This packet §4** | Still the correct path; unchanged commands |
| Go/no-go checklist | **This packet §8** | Replaces FOLLOWUP-6 §7; same items, EVO-005 confirmed blocked |
| Immediate action plan | **This packet §9** | Replaces FOLLOWUP-6 §8; adds stall escalation note (§5) |
| Worktree mirror discrepancy | **This packet §1** | New; identifies root cause of FOLLOWUP-5/6 discrepancy |
| Stall escalation | **This packet §5** | New; four consecutive packets without execution |

---

## 13. Sidecar Scope Constraints

This packet is a support artifact only. It:

- Does **not** modify any L1 canonical policy file
- Does **not** modify `ai-status.json`, `current-work.md`, or any loop registry
- Does **not** implement any BFF route, filter handler, or evidence collection
- Does **not** change the parent task's acceptance criteria
- Identifies the root cause (worktree mirror staleness) of the discrepancy that produced
  FOLLOWUP-5's incorrect state description
- Confirms FOLLOWUP-6 was accurate and that EVO-005 is still blocked in the same state
- Escalates to chair-review the four-packet stall on a two-command unblock sequence
- Must be absorbed into the parent task's final evidence packet at
  LOOP-AUTO-BFF-004 closeout alongside all prior sidecar packets

---

## 14. Change Log

| Date | Author | Change |
|---|---|---|
| 2026-06-27 | Claude | Initial FOLLOWUP-7 packet: live state re-audit confirms EVO-005 still blocked/Claude2 (FOLLOWUP-6 accurate); identifies worktree mirror staleness as root cause of FOLLOWUP-5 discrepancy; confirms all other 7 deps archived/done; reiterates FOLLOWUP-4 §2 unblock sequence; escalates to chair-review after four-packet stall on two-command unblock |
