# BFF Drill Readiness Consolidation: LOOP-AUTO-BFF-004 — FOLLOWUP-3

**Sidecar kind:** bff_handoff_packet (consolidated drill readiness)
**Parent task:** LOOP-AUTO-BFF-004
**Sidecar task:** LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3
**Prepared by:** Claude
**Date:** 2026-06-27
**Reviewer:** Claude2

---

## Purpose

This document consolidates findings from all prior sidecar packets into a single
pre-execution reference for Claude2 when picking up LOOP-AUTO-BFF-004.

Prior packets:
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF.md` — original gap analysis, operator
  journey specs, acceptance checklist
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-REVIEW.md` — reviewer confirmation of
  all 7 gaps; noted filter field gaps FG-001/FG-002
- `LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` — filter gap resolution
  spec, fallback procedures, updated maturity gate

Instead of cross-referencing three documents, Claude2 should use this packet as
the primary pre-drill checklist. Prior packets remain as supporting records.

This packet does **not** modify any L1 canonical policy, `ai-status.json`, the
loop registry, or any BFF implementation file.

---

## 1. Consolidated Gap Registry

All known gaps that block drill execution, with current resolution status.

### 1.1 BFF Surface Gaps (from HANDOFF §3, confirmed by Claude2 review)

| Gap ID | Route | Blocking drill | Owned by | Resolution status |
|---|---|---|---|---|
| SG-001 | `GET /api/v1/personas/{persona_id}/source-health` | Drill 1 | LOOP-AUTO-SRC-004 | Open — SRC-004 is `todo` |
| SG-002 | `GET /api/v1/source-connectors` (with `last_fetch_at`, `last_push_at`, `failure_reason`, `truth_source_label`) | Drill 1 | LOOP-AUTO-SRC-004 | Open — SRC-004 is `todo` |
| SG-003 | `GET /api/v1/loops` | Both drills | LOOP-AUTO-BFF-001 | Open — BFF-001 is `todo` |
| SG-004 | `GET /api/v1/loops/{loop_id}` | Both drills | LOOP-AUTO-BFF-001 | Open — BFF-001 is `todo` |
| SG-005 | `truth_source_label` field in source/persona surfaces | Drill 1 | LOOP-AUTO-BFF-003 | Open — BFF-003 is `todo` |
| SG-006 | Deployment stage split (approval/plan/saga/binding/runtime_fleet) in RT-01/DP-01 | Drill 2 | LOOP-AUTO-DEP-004 | Open — DEP-004 is `todo` |
| SG-007 | `dispatched_at`, `execution_result`, `blocked_reason` fields in EV-02 | Drill 2 | LOOP-AUTO-EVO-005 | Open — EVO-005 is `todo` |

**Pre-drill verification command for each gap:**
```bash
# SG-001
curl -s "$BFF_BASE/api/v1/personas/$PERSONA_ID/source-health" | jq .

# SG-002
curl -s "$BFF_BASE/api/v1/source-connectors" | jq '.[0] | keys'
# Expect: last_fetch_at, last_push_at, failure_reason, truth_source_label

# SG-003 / SG-004
curl -s "$BFF_BASE/api/v1/loops" | jq .
curl -s "$BFF_BASE/api/v1/loops/source_ingestion" | jq .

# SG-005
curl -s "$BFF_BASE/api/v1/personas/$PERSONA_ID/source-health" | jq '.[0].truth_source_label'

# SG-006
curl -s "$BFF_BASE/api/v1/runtimes/$RUNTIME_ID/status" | jq '{approval,plan,saga,binding,runtime_fleet}'

# SG-007
curl -s "$BFF_BASE/api/v1/evolution-decisions/$DECISION_ID" | jq '{dispatched_at,execution_result,blocked_reason}'
```

### 1.2 Filter Field Gaps (from HANDOFF-FOLLOWUP-2 §2)

| Gap ID | Route | Filter | Resolution status |
|---|---|---|---|
| FG-001 | `GET /api/v1/incidents` (IN-01) | `runtime_id` | Open — pending LOOP-AUTO-DEP-004 or LOOP-AUTO-BFF-004-FILTER-GAP |
| FG-002 | `GET /api/v1/evolution-decisions` (EV-01) | `incident_id` | Open — pending LOOP-AUTO-DEP-004 or LOOP-AUTO-BFF-004-FILTER-GAP |

**Quick check commands:**
```bash
# FG-001: test if runtime_id filter is accepted
curl -s "$BFF_BASE/api/v1/incidents?runtime_id=$RUNTIME_ID" | jq '.meta.filter_applied // "NOT APPLIED"'

# FG-002: test if incident_id filter is accepted
curl -s "$BFF_BASE/api/v1/evolution-decisions?incident_id=$INCIDENT_ID" | jq '.meta.filter_applied // "NOT APPLIED"'
```

---

## 2. Go/No-Go Checklist Before Starting Drills

Claude2 must run through this checklist before executing either drill. All items
must be green before submitting evidence.

### 2.1 Drill 1 (Source-to-Health) Prerequisites

```
[ ] SG-001 resolved: source-health sub-resource returns HTTP 200 with payload
[ ] SG-002 resolved: source-connectors list includes last_fetch_at, last_push_at, failure_reason, truth_source_label
[ ] SG-003 resolved: /api/v1/loops returns loop list with current_maturity field
[ ] SG-004 resolved: /api/v1/loops/source_ingestion returns loop detail
[ ] SG-005 resolved: truth_source_label appears in at least one response
[ ] BFF-001, SRC-004, BFF-003 all merged to dev and deployed to test env
[ ] Verification: git log --oneline origin/dev | grep -E 'BFF-001|SRC-004|BFF-003'
```

### 2.2 Drill 2 (Runtime-to-Incident-to-Evolution) Prerequisites

```
[ ] SG-006 resolved: runtime status shows 5 stage breakdown fields
[ ] SG-007 resolved: evolution decision shows dispatched_at, execution_result
[ ] FG-001 resolved OR fallback procedure documented (FOLLOWUP-2 §4.1)
[ ] FG-002 resolved OR fallback procedure documented (FOLLOWUP-2 §4.2)
[ ] TEL-005 replay corpus available in test env
[ ] DEP-004, TEL-005, EVO-005 all merged to dev and deployed to test env
[ ] Verification: git log --oneline origin/dev | grep -E 'DEP-004|TEL-005|EVO-005'
[ ] KNOW-006 consultation executor deployed (consultation gate check)
```

### 2.3 Filter Gap Decision (FG-001 and FG-002)

Before running Drill 2, Claude2 must make one of these decisions:

**Path A — Native filter (preferred):**
- Confirm `runtime_id` accepted by IN-01 and `incident_id` accepted by EV-01
- Record in evidence file: "FG-001 and FG-002 resolved via [task/commit]"

**Path B — Fallback filter (if native not yet available):**
- Use client-side filtering per FOLLOWUP-2 §4.1 and §4.2
- Add gap annotation block (FOLLOWUP-2 §4.3) at top of both evidence files
- Record: "FG-001 and FG-002 pending; using fallback client-side filter"
- Note: BFF-004 may reach `reconciled` with fallback; `proven-live` requires native filters

---

## 3. Evidence File Templates

Claude2 must produce two evidence files. Templates below provide the required
structure. Replace `<...>` with actual values from drill execution.

### 3.1 Drill 1 Evidence File

**Path:** `docs/deployment/evidence/LOOP-AUTO-BFF-004-drill1-source-health.md`

```markdown
# Drill 1 Evidence: Source-to-Health
# LOOP-AUTO-BFF-004

Date: <YYYY-MM-DD>
Executed by: Claude2
Environment: <test env name>
Dependency commit refs:
  BFF-001: <commit sha or PR>
  SRC-004: <commit sha or PR>
  BFF-003: <commit sha or PR>

## Step 1: Loop Health Check

Command:
  GET /api/v1/loops?loop_id=source_ingestion

Response:
  <paste curl output>

Verdict:
  current_maturity: <value>
  controller_health: <value>
  Pass/Fail: <>

## Step 2: Source Connector Panel

Command:
  GET /api/v1/source-connectors

Response:
  <paste curl output>

Verdict:
  At least one connector with non-null last_fetch_at: <yes/no>
  truth_source_label values seen: <list>
  Pass/Fail: <>

## Step 3: Persona Source-Health Panel

Command:
  GET /api/v1/personas/<persona_id>/source-health

Response:
  <paste curl output>

Verdict:
  Label distinction (live/scheduled/registry vs seed/fixture): <yes/no>
  Pass/Fail: <>

## Step 4: Overall Verdict

AC-D1-1 (dependencies deployed): <PASS/FAIL>
AC-D1-2 (query sequence completes): <PASS/FAIL>
AC-D1-3 (truth labels visible, non-seed): <PASS/FAIL>
AC-D1-4 (loop maturity >= scheduled): <PASS/FAIL>
AC-D1-5 (this file written): PASS

Drill 1 overall: <PASS/FAIL>
Maturity demonstrated: <api-only | scheduled | reconciled | proven-live>
```

### 3.2 Drill 2 Evidence File

**Path:** `docs/deployment/evidence/LOOP-AUTO-BFF-004-drill2-runtime-incident-evolution.md`

```markdown
# Drill 2 Evidence: Runtime-to-Incident-to-Evolution
# LOOP-AUTO-BFF-004

Date: <YYYY-MM-DD>
Executed by: Claude2
Environment: <test env name>
Dependency commit refs:
  DEP-004: <commit sha or PR>
  TEL-005: <commit sha or PR>
  EVO-005: <commit sha or PR>
  KNOW-006: <commit sha or PR>

## Filter Gap Status at Time of Drill

| Gap | Status | Workaround used |
|---|---|---|
| FG-001 runtime_id on IN-01 | PENDING / RESOLVED | Client-side filter / Native filter |
| FG-002 incident_id on EV-01 | PENDING / RESOLVED | Client-side filter / Native filter |

## Step 1: Observe Runtime Status

Command:
  GET /api/v1/runtimes/<runtime_id>/status

Response:
  <paste curl output>

Verdict:
  heartbeat_at: <value>
  Stage breakdown present (approval/plan/saga/binding/runtime_fleet): <yes/no>
  Pass/Fail: <>

## Step 2: Confirm Telemetry Anomaly

Command:
  GET /api/v1/telemetry/<runtime_id>/summary
  (using TEL-005 replay corpus)

Response:
  <paste curl output>

Verdict:
  Anomaly signal (heartbeat_loss or order_rejection_spike): <yes/no>
  Pass/Fail: <>

## Step 3: Confirm Incident Auto-Opened

Command:
  GET /api/v1/incidents?runtime_id=<runtime_id>&status=open
  [FALLBACK if FG-001 pending: GET /api/v1/incidents?status=open then client-filter]

Response:
  <paste curl output>

Verdict:
  Incident found within propagation window: <yes/no>
  severity: <value>
  trigger_source: <value>
  reconciliation_ids: <value>
  Pass/Fail: <>

## Step 4: Trace Incident Detail

Command:
  GET /api/v1/incidents/<incident_id>

Response:
  <paste curl output>

Verdict:
  postmortem_draft_id present (after resolution): <yes/no>
  Pass/Fail: <>

## Step 5: Trace to Evolution Proposal

Command:
  GET /api/v1/evolution-decisions?incident_id=<incident_id>
  [FALLBACK if FG-002 pending: GET /api/v1/evolution-decisions?status=proposed then client-filter]

Response:
  <paste curl output>

Verdict:
  Exactly one decision per incident+target cluster: <yes/no>
  status=proposed: <yes/no>
  action_type: <value>
  Pass/Fail: <>

## Step 6: Trace Evolution Follow-Through

Command:
  GET /api/v1/evolution-decisions/<decision_id>

Response:
  <paste curl output>

Verdict:
  Stage progression visible (proposed→reviewed→approved→dispatched→executed): <yes/no>
  dispatched_at: <value>
  execution_result: <value>
  blocked_reason: <value if blocked>
  Pass/Fail: <>

## Step 7: Overall Verdict

AC-D2-1 (dependencies deployed): <PASS/FAIL>
AC-D2-2 (runtime stage breakdown): <PASS/FAIL>
AC-D2-3 (anomaly → incident): <PASS/FAIL>
AC-D2-4 (incident → evolution proposal): <PASS/FAIL>
AC-D2-5 (evolution follow-through stages): <PASS/FAIL>
AC-D2-6 (this file written): PASS

Drill 2 overall: <PASS/FAIL>
Filter gap path taken: <native filter / fallback client-side>
Maturity demonstrated: <api-only | scheduled | reconciled | proven-live>
Propagation latency (anomaly → incident): <value>
Propagation latency (incident → evolution proposal): <value>
```

---

## 4. Optimal Drill Execution Sequence

Based on dependency task groupings, Claude2 should execute drills in this order
when dependencies complete in waves:

### Wave A: SRC-004 + BFF-001 + BFF-003 complete first
- Run Drill 1 immediately
- File Drill 1 evidence
- Note: Drill 2 preparation can begin in parallel (no conflict)

### Wave B: DEP-004 + TEL-005 + EVO-005 + KNOW-006 complete
- Run Drill 2
- If FG-001/FG-002 still open: use fallback procedures from FOLLOWUP-2 §4
- File Drill 2 evidence

### Not recommended: Wait for all 7 dependencies before starting any drill
- This delays Drill 1 unnecessarily
- Drill 1 has no dependency on Drill 2 surfaces
- Running them independently produces cleaner, attributable evidence

### If only some Wave B tasks complete:
- DEP-004 is the most critical for Drill 2: without stage-split, Step 1 fails AC-D2-2
- TEL-005 is required for the anomaly trigger (no replay corpus = no repeatable evidence)
- EVO-005 is required for Steps 5 and 6 (follow-through fields)
- KNOW-006 only gates consultation paths; Drill 2 can run without it if the drill
  does not touch a loop that routes through consultation

---

## 5. Maturity Statement Template

The closeout evidence packet for LOOP-AUTO-BFF-004 must include one of these
maturity statements:

**If both drills pass with native filters:**
```
LOOP-AUTO-BFF-004 reached `proven-live` maturity on YYYY-MM-DD.
Evidence: LOOP-AUTO-BFF-004-drill1-source-health.md,
          LOOP-AUTO-BFF-004-drill2-runtime-incident-evolution.md.
All acceptance criteria AC-D1-1 through AC-D2-6 satisfied.
Filter gaps FG-001 and FG-002 resolved (native filter in BFF_API_CONTRACT).
```

**If Drill 2 used fallback filter procedures:**
```
LOOP-AUTO-BFF-004 reached `reconciled` maturity on YYYY-MM-DD.
Evidence: LOOP-AUTO-BFF-004-drill1-source-health.md,
          LOOP-AUTO-BFF-004-drill2-runtime-incident-evolution.md.
Drill 2 Steps 3 and 5 used client-side fallback filter (FG-001 and FG-002 pending).
Promotion to `proven-live` requires FG-001 and FG-002 resolved in BFF_API_CONTRACT.
Remaining blockers: [LOOP-AUTO-DEP-004 or LOOP-AUTO-BFF-004-FILTER-GAP]
```

**If Drill 1 only (Wave B dependencies not yet complete):**
```
LOOP-AUTO-BFF-004 partially executed on YYYY-MM-DD.
Drill 1 (source-to-health): PASS / maturity evidence at <drill1 file>
Drill 2 (runtime-to-incident-to-evolution): BLOCKED — <list of missing dep tasks>
Current maturity claim: `reconciled` (unchanged; Drill 2 evidence required for promotion)
```

---

## 6. Known Risks and Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Replay corpus (TEL-005) not available in test env when Drill 2 runs | Medium | Check `GET /api/v1/telemetry/corpus-status` before Step 2; escalate to TEL-005 owner if absent |
| Incident not auto-opened within propagation window | Medium | Check incident service health first; use `GET /api/v1/incidents?status=open` with a 5-min wait |
| Evolution proposal not generated (EVO-005 gap) | High if EVO-005 incomplete | Drill 2 Steps 5+6 cannot be satisfied; note as blocker in evidence and retry after EVO-005 merge |
| FG-001/FG-002 rejected with 400 (filter not in allowlist) | Medium | Switch to fallback path; annotate evidence per FOLLOWUP-2 §4.3 |
| Consultation gate blocks drill path | Low | KNOW-006 only gates consultation-routed loops; skip consultation-touching loops unless required for AC coverage |
| `truth_source_label` not present in BFF-003 deploy | Medium | Escalate to BFF-003 owner; Drill 1 Step 3 cannot satisfy AC-D1-3 without this field |

---

## 7. Reviewer Guidance (for Claude2 reviewing this packet)

When reviewing FOLLOWUP-3:

- Confirm that the go/no-go checklist in Section 2 correctly references all acceptance
  criteria from the original packet (HANDOFF §6).
- Confirm that evidence file templates in Section 3 produce evidence that satisfies
  every AC in HANDOFF §6.1 and §6.2.
- Confirm that the maturity statement templates in Section 5 accurately describe
  the outcomes (proven-live vs reconciled) per the maturity gate in HANDOFF §7.
- If any risk in Section 6 has already occurred or been resolved, note it in the
  change log below with date and resolution.
- This packet does not need to be re-reviewed if only Section 3 (templates) is used
  as-is to fill in evidence; the template is normative.

---

## 8. Cross-Reference Map

| Topic | Primary source | Quick pointer |
|---|---|---|
| Full gap analysis | HANDOFF §3 | 7 gaps; all confirmed by Claude2 review |
| Filter gap resolution spec | FOLLOWUP-2 §2 | FG-001 add `runtime_id` to IN-01; FG-002 add `incident_id` to EV-01 |
| Fallback procedures | FOLLOWUP-2 §4 | Client-side filter; gap annotation block |
| Operator journey steps | HANDOFF §4 | Drill 1: 4 steps; Drill 2: 7 steps |
| Frontend panel readiness | HANDOFF §5 | Panel-by-panel BFF surface dependency |
| Full acceptance checklist | HANDOFF §6 | AC-D1-1 through AC-D2-6 |
| Maturity gate | HANDOFF §7 | reconciled → proven-live requires both drills |
| Ownership recommendation | FOLLOWUP-2 §3 | Option A: add to DEP-004; Option B: new filter-gap task |
| Field name corrections | FOLLOWUP-2 §10 | `runtime_id` (not `affected_runtime_id`); `linked_incident_id` (not `source_incident_id`) |

---

## 9. Sidecar Scope Constraints

This packet is a support artifact only. It:

- Does **not** modify any L1 canonical policy file
- Does **not** modify `ai-status.json`, `current-work.md`, or any loop registry
- Does **not** implement any BFF route, filter handler, or evidence collection
- Does **not** change the parent task's acceptance criteria (documents and templates them)
- Consolidates and synthesizes information from prior sidecar packets only
- Must be absorbed into the parent task's final evidence packet at
  LOOP-AUTO-BFF-004 closeout alongside all prior sidecar packets

---

## 10. Change Log

| Date | Author | Change |
|---|---|---|
| 2026-06-27 | Claude | Initial FOLLOWUP-3 packet: consolidated gap registry, go/no-go checklist, evidence file templates, drill execution sequence, maturity statement templates, risk register, cross-reference map |
