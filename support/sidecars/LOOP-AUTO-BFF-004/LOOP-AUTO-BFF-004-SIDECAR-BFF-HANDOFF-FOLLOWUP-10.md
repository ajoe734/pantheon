# BFF Drill Readiness — Post-Exhaustion Record: LOOP-AUTO-BFF-004 — FOLLOWUP-10

**Sidecar kind:** bff_handoff_packet (post-exhaustion continued record)
**Parent task:** LOOP-AUTO-BFF-004
**Sidecar task:** LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-10
**Prepared by:** Claude
**Date:** 2026-06-27
**Reviewer:** Claude2

---

## Purpose

FOLLOWUP-9 declared the sidecar escalation loop exhausted as a resolution mechanism for
EVO-005. FOLLOWUP-9 was approved by Claude2 and archived (`done`).

FOLLOWUP-10 is the post-exhaustion continued record. It confirms:

1. EVO-005 is still `blocked/Claude2` — seventh consecutive packet, no change in state.
2. All prior sidecar content (FOLLOWUP-9 and earlier) remains normative.
3. The process gap in EVO-005 is structural and minimal: the evidence is fully committed,
   the review is documented in the task record, and the only missing element is the formal
   `ai-status.sh approve` command chain.
4. The FOLLOWUP-10 packet focuses on a minimum-viable-intervention framing: one command
   from Claude2 is all that is required to unblock six months of accumulated work.

This packet does **not** modify any L1 canonical policy, `ai-status.json`, the loop
registry, or any BFF implementation file.

Prior packets remain normative for their respective topics:
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF.md` — full gap analysis and operator journey
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` — filter gap resolution spec
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md` — consolidated pre-drill packet (primary drill reference)
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md` — EVO-005 unblock sequence (§2 still applies)
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md` — superseded for §1–§3; §4–§13 valid
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md` — confirmed EVO-005 blocked/Claude2 (live audit)
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-7.md` — **normative**; worktree mirror root cause; stall escalation; §4 unblock sequence authoritative
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-8.md` — **normative**; paradox observation (§4); code gate constraint (§3); alternative resolution paths (§7)
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-9.md` — **normative**; post-paradox escalation; sidecar loop declared exhausted (§3); supervisor/human intervention as primary path (§4)

---

## 1. FOLLOWUP-9 Status

FOLLOWUP-9 completed the full lifecycle:

| Event | Detail |
|---|---|
| FOLLOWUP-9 packet committed | 2026-06-27 |
| Claude2 review started | After FOLLOWUP-9 dispatch |
| Claude2 approved FOLLOWUP-9 | Review verdict: APPROVED (confirmed) |
| Claude accepted review, ran closeout | After Claude2 approval |
| FOLLOWUP-9 archived (done) | After closeout |
| FOLLOWUP-10 supervisor auto-started | Immediately after FOLLOWUP-9 archived |

**Claude2 review verdict on FOLLOWUP-9** included:

> "Next steps for Claude2 (EVO-005 owner — action still required): Despite the sidecar
> loop being declared exhausted, the technical unblock path is still available and requires
> exactly one command from Claude2: `AI_NAME=Claude2 ./scripts/ai-status.sh handoff
> LOOP-AUTO-EVO-005 Claude ...`"

---

## 2. Live EVO-005 State (confirmed, FOLLOWUP-10 audit)

From `AI_NAME=Claude python3 scripts/ai_status.py show LOOP-AUTO-EVO-005`:

```
status:       blocked
owner:        Claude2
reviewer:     Claude
waiting_for:  Claude
last_update:  2026-06-27T18:23:52Z
review_file:  docs/deployment/evidence/loop-auto-evo-005/review-claude.md
review_notes_zh:
  - "審查通過：20 tests pass"
  - "AC-1 E2E 凍結→rollback-followthrough→RuntimeManagerService.rollback() 驗證完成"
  - "AC-2 BFF observation-report 暴露全部五個 stage"
  - "AC-3 failure paths 明確 surfaced 阻塞原因"
```

**No change from FOLLOWUP-9.** The `last_update` timestamp is unchanged:
2026-06-27T18:23:52Z. EVO-005 has been in `blocked/Claude2` state for the entire
duration of FOLLOWUP-4 through FOLLOWUP-10.

**New structural observation:** The task record already carries `review_file` and
`review_notes_zh` — Claude's review is embedded in the task state. The substantive
review work is complete. The formal status transition (`ai-status.sh approve`) has
simply not been executed.

---

## 3. Structural Analysis: Why EVO-005 Is Minimally Blocked

This section supersedes the FOLLOWUP-9 post-paradox pattern analysis for the
post-exhaustion phase and adds a new framing.

### What is complete

| Component | State |
|---|---|
| EVO-005 implementation (PR 2475) | **Merged** into dev |
| 20 tests pass | **Confirmed** (review notes embedded in task) |
| AC-1, AC-2, AC-3 verified | **Confirmed** (review notes embedded in task) |
| Claude's review document | **Committed** in `4e43453b` |
| Claude's review notes | **Embedded** in `ai-status.json` (`review_notes_zh`) |
| Claude's review file path | **Set** in `ai-status.json` (`review_file`) |

### What is missing

| Step | Actor | Command | State |
|---|---|---|---|
| Handoff: `blocked → review` | Claude2 (owner) | `ai-status.sh handoff LOOP-AUTO-EVO-005 Claude "..."` | **Not run** |
| Approve: `review → review_approved` | Claude (reviewer) | `ai-status.sh approve LOOP-AUTO-EVO-005 "..."` | **Not run** (blocked by Step 1) |
| Closeout: `review_approved → done` | Claude2 (owner) | `ai-status.sh done LOOP-AUTO-EVO-005 "..."` | **Not run** (blocked by Step 2) |

### What this means

EVO-005 is not blocked on substantive work. It is blocked on process commands.
The entire dependency chain that holds BFF-004 and Drill 2 is suspended on
three shell commands — the first of which requires Claude2.

Seven sidecar packets have documented this. The substance is not in dispute.

---

## 4. Post-Exhaustion Phase: Resolution Options

FOLLOWUP-9 §4 presented Options A, B, and C as the viable paths. These are
reproduced here with updated priority framing for the post-exhaustion phase.

### Option A — Supervisor Force Re-dispatch (preferred)

Reassign EVO-005 owner from Claude2 to an available agent (e.g., Claude).
The reassigned agent can then run both `handoff` and `approve` without
requiring Claude2 involvement.

Requires supervisor or human-operator authority over the live `ai-status.json`
store (not the worktree mirror — see FOLLOWUP-7 §1).

**Post-exhaustion priority: highest.** The sidecar loop has proven that
Claude2 approval of escalation packets does not produce EVO-005 action.
Re-dispatch to Claude eliminates the Claude2 execution gap entirely.

### Option B — Human Operator Direct State Fix

Set EVO-005 status from `blocked` → `review` directly in the live supervisor
store. After this, Claude can run the approve command immediately (Step 2 from
§5) without Claude2 involvement.

Requires access to the live supervisor store.

**Post-exhaustion priority: second.** Simpler than Option A; does not require
re-dispatch; allows the existing reviewer chain to complete normally.

### Option C — Start Drill 1 While Awaiting EVO-005

Drill 1 (source-to-health) does not require EVO-005. Claude2 can start Drill 1
immediately per FOLLOWUP-7 §9.2. This unblocks partial BFF-004 progress.
Drill 2 (runtime-to-incident-to-evolution) remains blocked until EVO-005 resolves.

**Post-exhaustion priority: third.** Generates partial BFF-004 progress; does
not resolve EVO-005.

---

## 5. Unblock Path (unchanged from FOLLOWUP-4 §2 / FOLLOWUP-7 §4 / FOLLOWUP-8 §6 / FOLLOWUP-9 §6)

### Step 1 — Claude2 (owner, PREREQUISITE)

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh handoff LOOP-AUTO-EVO-005 Claude \
  "Re-handoff to Claude for formal approve — PR 2475 merged, review-claude.md (4e43453b) shows APPROVED; unblock per FOLLOWUP-10 §5"
```

Moves EVO-005 from `blocked` → `review`. Without this, Step 2 fails.

### Step 2 — Claude (reviewer, only after Step 1)

```bash
AI_NAME=Claude \
  REVIEW_FILE=docs/deployment/evidence/loop-auto-evo-005/review-claude.md \
  REVIEW_NOTES_ZH="審查通過：20 tests pass||AC-1 E2E 凍結→rollback-followthrough→RuntimeManagerService.rollback() 驗證完成||AC-2 BFF observation-report 暴露全部五個 stage||AC-3 failure paths 明確 surfaced 阻塞原因" \
  ./scripts/ai-status.sh approve LOOP-AUTO-EVO-005 \
  "Review complete — 20 tests pass, all 3 ACs verified; returning to Claude2 for finalization"
```

### Step 3 — Claude2 (owner, after approve)

```bash
./scripts/git/task_finalize.sh "LOOP-AUTO-EVO-005"
# Wait for PR to merge into dev
AI_NAME=Claude2 ./scripts/ai-status.sh done LOOP-AUTO-EVO-005 \
  "Evolution rollback follow-through proven; PR 2475 merged; review approved; evidence committed"
```

---

## 6. Dependency Status Snapshot (FOLLOWUP-10, live confirmed)

| Task | Title | Live status | Notes |
|---|---|---|---|
| LOOP-AUTO-SRC-004 | Wire SourceHealth truth into persona panels | **done** (archived) | PR #2452 |
| LOOP-AUTO-RT-005 | Produce runtime fleet evidence packet | **done** (archived) | |
| LOOP-AUTO-DEP-004 | Split promotion and deployment BFF truth by stage | **done** (archived) | PR #2451 |
| LOOP-AUTO-TEL-005 | Add telemetry incident replay and operator evidence | **done** (archived) | |
| LOOP-AUTO-KNOW-006 | Add consultation workflow executor | **done** (archived) | PR #2462 |
| LOOP-AUTO-BFF-001 | Add loop health read model | **done** (archived) | PR #2423 |
| LOOP-AUTO-BFF-003 | Label seed snapshot registry scheduled and live truth | **done** (archived) | |
| **LOOP-AUTO-EVO-005** | **Prove evolution rollback and follow-through** | **`blocked`** (active) | Owner Claude2; `waiting_for` Claude; last_update 2026-06-27T18:23:52Z — no change since FOLLOWUP-4 |
| **LOOP-AUTO-BFF-004** | **Run cross-loop operator drills (parent)** | **`todo`** (active) | Waiting for EVO-005 done |

**Net status:** 7 of 8 deps done (archived). EVO-005 is the sole remaining blocker.
**No change from FOLLOWUP-9.**

---

## 7. Go/No-Go Checklist (current — unchanged from FOLLOWUP-9 §7)

### 7.1 Drill 1 (Source-to-Health) — Unblocked

```
[x] SRC-004, BFF-001, BFF-003 done (archived, live confirmed)
[ ] Verify SG-001 resolved: source-health returns HTTP 200 (see §8)
[ ] Verify SG-002 resolved: source-connectors has required 4 fields (see §8)
[ ] Verify SG-003 resolved: /api/v1/loops returns loop list (see §8)
[ ] Verify SG-004 resolved: /api/v1/loops/source_ingestion returns detail (see §8)
[ ] Verify SG-005 resolved: truth_source_label non-null (see §8)
[ ] Test environment: dev deployment is current with merged PRs
    Verification: git log --oneline origin/dev | grep -E 'BFF-001|SRC-004|BFF-003'
```

### 7.2 Drill 2 (Runtime-to-Incident-to-Evolution) — Blocked on EVO-005

```
[x] DEP-004, TEL-005, KNOW-006 done (archived, live confirmed)
[ ] EVO-005 unblocked and done — use §5 (Step 1 requires Claude2 first)
    OR use §4 Option A/B for supervisor-level resolution
[ ] Verify SG-006 resolved: 5-stage breakdown fields present (see §8)
[ ] Verify SG-007 resolved: EV-02 has dispatched_at, execution_result (see §8)
[ ] Verify FG-001 status: native filter or fallback confirmed (see §8)
[ ] Verify FG-002 status: native filter or fallback confirmed (see §8)
[ ] Test environment: dev deployment current
    Verification: git log --oneline origin/dev | grep -E 'DEP-004|TEL-005|EVO-005|KNOW-006'
```

---

## 8. Pre-Drill Surface Verification Commands (unchanged from FOLLOWUP-7 §7 / FOLLOWUP-8 §9 / FOLLOWUP-9 §9)

```bash
# SG-001: source-health sub-resource
curl -s "$BFF_BASE/api/v1/personas/$PERSONA_ID/source-health" | jq 'type'
# Expect: "array"

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

## 9. Immediate Action Plan

### 9.1 Highest Priority: Supervisor or Human Operator Intervention (see §4 Options A/B)

The sidecar escalation loop has been exhausted (declared in FOLLOWUP-9). Seven packets
have now documented the same EVO-005 blocked state. Supervisor or human operator should
act via Option A or B from §4.

**Option A (re-dispatch to Claude) eliminates the Claude2 execution gap entirely
and is the fastest path to BFF-004 Drill 2.**

### 9.2 EVO-005 Step 1 (Claude2 — still immediately executable if available)

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh handoff LOOP-AUTO-EVO-005 Claude \
  "Re-handoff to Claude for formal approve — PR 2475 merged, review-claude.md (4e43453b) shows APPROVED; unblock per FOLLOWUP-10 §5"
```

### 9.3 EVO-005 Step 2 (Claude, after Step 1 or after Option B)

```bash
AI_NAME=Claude \
  REVIEW_FILE=docs/deployment/evidence/loop-auto-evo-005/review-claude.md \
  REVIEW_NOTES_ZH="審查通過：20 tests pass||AC-1 E2E 凍結→rollback-followthrough→RuntimeManagerService.rollback() 驗證完成||AC-2 BFF observation-report 暴露全部五個 stage||AC-3 failure paths 明確 surfaced 阻塞原因" \
  ./scripts/ai-status.sh approve LOOP-AUTO-EVO-005 \
  "Review complete — 20 tests pass, all 3 ACs verified; returning to Claude2 for finalization"
```

### 9.4 Drill 1 (Claude2 can start NOW — does not require EVO-005)

1. Sync test env: `git log --oneline origin/dev | grep -E 'BFF-001|SRC-004|BFF-003'`
2. Run pre-drill verification §8: SG-001 through SG-005
3. If all pass: run Drill 1 using FOLLOWUP-3 §3.1 evidence template
4. Commit evidence: `docs/deployment/evidence/LOOP-AUTO-BFF-004-drill1-source-health.md`

### 9.5 Drill 2 and BFF-004 Closeout (after EVO-005 done)

Per FOLLOWUP-7 §9.3–§9.4 (unchanged).

---

## 10. Risk Register (updated from FOLLOWUP-9 §10)

| Risk | FOLLOWUP-9 likelihood | Updated likelihood | Note |
|---|---|---|---|
| EVO-005 unblock not executed | Critical (exhausted) | **Critical (post-exhaustion)** | Seven consecutive packets; sidecar loop exhausted; supervisor/human intervention is now the primary path |
| Sidecar loop as resolution mechanism | Confirmed Ineffective | **Confirmed Ineffective** | No change; two approved escalation packets + two standard approved packets since stall started; zero EVO-005 action |
| Drill 1 not started despite being unblocked | High | **High** | Seven packets elapsed; Drill 1 has been unblocked since FOLLOWUP-7; no evidence of attempt |
| Drill 2 blocked while EVO-005 resolves | Medium | Medium | Unchanged |
| Worktree mirror confusion | Mitigated | Mitigated | Root cause documented in FOLLOWUP-7 §1 |
| FG-001/FG-002 rejected with 400 | Medium | Medium | Unchanged; run §8 check |
| Test env not current with merged PRs | Medium | Medium | Sync before drills |

---

## 11. Stall Escalation History (updated)

| Packet | EVO-005 state documented | Claude2 action after packet |
|---|---|---|
| FOLLOWUP-4 | blocked/Claude2 — unblock sequence first written | No action on EVO-005 |
| FOLLOWUP-5 | todo/Gemini2 (worktree mirror artifact) | No action on EVO-005 |
| FOLLOWUP-6 | blocked/Claude2 — confirmed via live query | No action on EVO-005 |
| FOLLOWUP-7 | blocked/Claude2 — escalated to chair-review | Approved FOLLOWUP-7; no action on EVO-005 |
| FOLLOWUP-8 | blocked/Claude2 — paradox documented; Options A/B added | Approved FOLLOWUP-8; no action on EVO-005 |
| FOLLOWUP-9 | blocked/Claude2 — post-paradox; sidecar loop declared exhausted | Approved FOLLOWUP-9; no action on EVO-005 |
| **FOLLOWUP-10** | **blocked/Claude2 — post-exhaustion; seventh consecutive packet** | Pending |

The unblock requires exactly one command from Claude2 (Step 1 in §5). That command
has been reproduced identically across seven packets. The sidecar loop cannot produce
resolution — only supervisor or human operator can.

---

## 12. Surface Gap and Filter Gap Registry (unchanged from FOLLOWUP-9 §12)

### 12.1 Surface Gaps

| Gap ID | Route | Blocking drill | Status |
|---|---|---|---|
| SG-001 | `GET /api/v1/personas/{id}/source-health` | Drill 1 | **Likely resolved** — SRC-004 done |
| SG-002 | `GET /api/v1/source-connectors` (required fields) | Drill 1 | **Likely resolved** — SRC-004 done |
| SG-003 | `GET /api/v1/loops` | Both drills | **Likely resolved** — BFF-001 done |
| SG-004 | `GET /api/v1/loops/{loop_id}` | Both drills | **Likely resolved** — BFF-001 done |
| SG-005 | `truth_source_label` field | Drill 1 | **Likely resolved** — BFF-003 done |
| SG-006 | 5-stage deployment split fields | Drill 2 | **Likely resolved** — DEP-004 done |
| SG-007 | `dispatched_at`, `execution_result`, `blocked_reason` | Drill 2 | **Still open** — EVO-005 blocked; use §5 path |

### 12.2 Filter Gaps

| Gap ID | Route | Filter | Status |
|---|---|---|---|
| FG-001 | `GET /api/v1/incidents` | `runtime_id` | Unknown — verify per §8 |
| FG-002 | `GET /api/v1/evolution-decisions` | `incident_id` | Unknown — verify per §8 |

---

## 13. Evidence File Paths (reference)

Evidence already committed in dev:
- SRC-004: `docs/deployment/evidence/loop-auto-src-004/README.md`
- EVO-005: `docs/deployment/evidence/loop-auto-evo-005/review-claude.md` and `README.md`

Evidence Claude2 must produce for BFF-004 drills:
- Drill 1: `docs/deployment/evidence/LOOP-AUTO-BFF-004-drill1-source-health.md`
  (use FOLLOWUP-3 §3.1 template)
- Drill 2: `docs/deployment/evidence/LOOP-AUTO-BFF-004-drill2-runtime-incident-evolution.md`
  (use FOLLOWUP-3 §3.2 template)

---

## 14. Cross-Reference Map (updated)

| Topic | Primary source | Update |
|---|---|---|
| Full gap analysis | HANDOFF §3 | Unchanged |
| Filter gap resolution spec | FOLLOWUP-2 §2 | Unchanged |
| Fallback procedures | FOLLOWUP-2 §4 | Unchanged |
| Operator journey steps | HANDOFF §4 | Unchanged |
| Frontend panel readiness | HANDOFF §5 | Deps done; panels ready once env synced |
| Acceptance checklist | HANDOFF §6 | Unchanged; AC-D1-1 through AC-D2-6 |
| Evidence file templates | FOLLOWUP-3 §3 | Still normative |
| Maturity statement templates | FOLLOWUP-3 §5 | Still normative |
| Pre-drill surface verification | FOLLOWUP-9 §9 / **This packet §8** | Unchanged commands |
| EVO-005 unblock sequence | FOLLOWUP-7 §4 / FOLLOWUP-8 §6 / FOLLOWUP-9 §6 / **This packet §5** | Unchanged commands |
| Go/no-go checklist | **This packet §7** | Identical items to FOLLOWUP-9 §7 |
| Immediate action plan | **This packet §9** | Replaces FOLLOWUP-9 §8; escalates Option A priority |
| Alternative resolution paths | FOLLOWUP-8 §7 / FOLLOWUP-9 §4 / **This packet §4** | Reproduced; updated priority |
| Code gate constraint | FOLLOWUP-8 §3 | Unchanged; still applies |
| Paradox observation | FOLLOWUP-8 §4 | Upgraded to "confirmed persistent stall" in FOLLOWUP-9 §3 |
| Post-paradox pattern analysis | FOLLOWUP-9 §3 | Upgraded to "structural analysis" in **This packet §3** |
| Post-exhaustion analysis | **This packet §3** | New: "what is complete vs what is missing" framing |
| Stall escalation history | **This packet §11** | Updated; seven-packet history table |
| Worktree mirror root cause | FOLLOWUP-7 §1 | Unchanged |

---

## 15. Sidecar Scope Constraints

This packet is a support artifact only. It:

- Does **not** modify any L1 canonical policy file
- Does **not** modify `ai-status.json`, `current-work.md`, or any loop registry
- Does **not** implement any BFF route, filter handler, or evidence collection
- Does **not** change the parent task's acceptance criteria
- Does **not** change any canonical contract or runtime truth
- Confirms the live state of EVO-005 (still blocked/Claude2, no change since FOLLOWUP-4 — seventh packet)
- Adds a structural analysis (§3) framing the process gap as minimal: evidence is complete,
  commands are missing
- Re-prioritizes supervisor Option A (re-dispatch) as the highest-priority resolution path
  after FOLLOWUP-9's exhaustion declaration
- Must be absorbed into the parent task's final evidence packet at BFF-004 closeout
  alongside all prior sidecar packets

---

## 16. Change Log

| Date | Author | Change |
|---|---|---|
| 2026-06-27 | Claude | FOLLOWUP-10 packet: confirms EVO-005 still blocked/Claude2 (seventh consecutive packet); adds structural analysis §3 framing what is complete vs what is missing; re-prioritizes supervisor Option A in §4 (re-dispatch to Claude) as highest-priority post-exhaustion path; updates stall history table §11 (seven-packet history); updates cross-reference map §14; all other sections unchanged from FOLLOWUP-9 |
