# BFF Handoff Packet: LOOP-AUTO-BFF-004
# Run cross-loop operator drills

**Sidecar kind:** bff_handoff_packet
**Parent task:** LOOP-AUTO-BFF-004
**Sidecar task:** LOOP-AUTO-BFF-004-SIDECAR-BFF-HANDOFF
**Prepared by:** Claude
**Date:** 2026-06-27
**Reviewer:** Claude2

---

## 1. Task Identity

| Field | Value |
|---|---|
| Task ID | LOOP-AUTO-BFF-004 |
| Title | Run cross-loop operator drills |
| Phase | Global Loop Autopilot / Wave 7 BFF Operator Truth |
| Owner | Claude2 |
| Reviewer | Codex |
| Status | todo |
| Loop IDs | all |
| Current maturity | reconciled |
| Target maturity | proven-live |
| Wave | Wave 7 BFF Operator Truth |

---

## 2. Dependency Map

### 2.1 Direct Dependencies

| Task ID | Title | Owner | Status | Why needed |
|---|---|---|---|---|
| LOOP-AUTO-SRC-004 | Wire SourceHealth truth into persona panels | Codex2 | todo | SourceHealth read model must be live in BFF for drill 1 (source-to-health) |
| LOOP-AUTO-RT-005 | Produce runtime fleet evidence packet | Codex2 | todo | Runtime fleet evidence required for drill 2 (runtime-to-incident-to-evolution) |
| LOOP-AUTO-DEP-004 | Split promotion and deployment BFF truth by stage | Codex2 | todo | BFF must expose approval/plan/saga/binding/runtime fleet stages separately before drill 2 |
| LOOP-AUTO-TEL-005 | Add telemetry incident replay and operator evidence | Gemini2 | todo | Telemetry incident replay corpus required to trigger drill 2 path |
| LOOP-AUTO-EVO-005 | Prove evolution rollback and follow-through | Gemini2 | todo | Evolution follow-through evidence required for drill 2 terminal stage |
| LOOP-AUTO-KNOW-006 | Add consultation workflow executor | Claude | todo | ConsultRequest/ConsultMemo handoff must be durable before any drill path touches consultation |
| LOOP-AUTO-BFF-003 | Label seed snapshot registry scheduled and live truth | Codex2 | todo | Operator panels must label truth sources correctly before drill 1 can be valid |

### 2.2 Upstream Dependency Chain (abbreviated)

```
LOOP-AUTO-000  (loop catalog schema)
  ├─ LOOP-AUTO-SRC-001 → SRC-002 → SRC-003 → SRC-004  ← drill 1 input
  ├─ LOOP-AUTO-RT-001 → RT-002 → RT-003 → RT-004 → RT-005  ← drill 2 fleet proof
  ├─ LOOP-AUTO-DEP-001 → DEP-002 → DEP-003 → DEP-004  ← drill 2 deployment stage split
  ├─ LOOP-AUTO-TEL-001 → TEL-002 → TEL-003 → TEL-004 → TEL-005  ← drill 2 replay corpus
  ├─ LOOP-AUTO-EVO-002 → EVO-003 → EVO-004 → EVO-005  ← drill 2 evolution follow-through
  ├─ LOOP-AUTO-KNOW-001 → KNOW-006  ← consultation gate
  └─ LOOP-AUTO-BFF-001 → BFF-002 → BFF-003  ← BFF surface readiness
        └─ LOOP-AUTO-BFF-004  ← THIS TASK
```

### 2.3 Downstream Dependents

LOOP-AUTO-BFF-004 is the final leaf task of Wave 7 and of the Global Loop Autopilot program. There are no downstream task dependencies. Its output is the wave closeout evidence packet.

---

## 3. BFF Query Gaps for Cross-Loop Drills

This section identifies which BFF surfaces are needed for each drill and which are not yet confirmed as implemented. Gaps are listed as queries that Claude2 must verify or escalate before starting the drills.

### 3.1 Drill 1 — Source-to-Health Flow

**Drill goal**: Prove that a source-ingestion run flows through SourceHealth truth into a truthfully labeled operator panel.

| Surface ID | Route | Status | Delivered by | Notes |
|---|---|---|---|---|
| SH-01 | `GET /api/v1/personas/{persona_id}/source-health` | **GAP** | LOOP-AUTO-SRC-004 | SourceHealth per-persona connector view; requires SRC-004 merge |
| SH-02 | `GET /api/v1/source-connectors` | **GAP** | LOOP-AUTO-SRC-004 | Connector list with schedule, last_fetch_at, last_push_at, failure_reason |
| LH-01 | `GET /api/v1/loops` | **GAP** | LOOP-AUTO-BFF-001 | Loop health read model; required for maturity display |
| LH-02 | `GET /api/v1/loops/{loop_id}` | **GAP** | LOOP-AUTO-BFF-001 | Loop detail with controller_health, last_success, last_failure, evidence |
| BFF-003-label | Truth label fields in existing surfaces | **PARTIAL** | LOOP-AUTO-BFF-003 | Seed/snapshot/registry/live labels must be visible in SH-01 and PS-06 |

**Query sequence for drill 1:**
```
GET /api/v1/loops?loop_id=source_ingestion
→ GET /api/v1/source-connectors
→ GET /api/v1/personas/{persona_id}/source-health
→ confirm: truth_source_label in {live, scheduled, registry} — not seed/fixture
```

**Pre-condition**: LOOP-AUTO-SRC-004 and LOOP-AUTO-BFF-001 and LOOP-AUTO-BFF-003 must all be merged and deployed to the test environment before this drill can run.

### 3.2 Drill 2 — Runtime-to-Incident-to-Evolution-Proposal Flow

**Drill goal**: Prove that a runtime anomaly (heartbeat loss or order rejection spike) flows through incident creation and into an evolution proposal, observable across BFF panels at each stage.

| Surface ID | Route | Status | Delivered by | Notes |
|---|---|---|---|---|
| RT-01 | `GET /api/v1/runtime-bindings` | Existing | Prior BFF work | List active bindings with deployment_mode, artifact, stage |
| RT-03 | `GET /api/v1/runtimes/{runtime_id}/status` | Existing | Prior BFF work | Current runtime state incl. heartbeat freshness |
| TL-02 | `GET /api/v1/telemetry/{runtime_id}/summary` | Existing | Prior BFF work | Aggregated telemetry summary |
| IN-01 | `GET /api/v1/incidents` | Existing | Prior BFF work | Active and historical incidents |
| IN-02 | `GET /api/v1/incidents/{incident_id}` | Existing | Prior BFF work | Full incident with evidence, mitigation |
| DEP-stage | Stage-split deployment fields in RT-01/DP-01 | **PARTIAL** | LOOP-AUTO-DEP-004 | approval/plan/saga/binding/fleet must be exposed separately |
| EV-01 | `GET /api/v1/evolution-decisions` | Existing | Prior BFF work | Decisions with status, action_type, risk_level |
| EV-02 | `GET /api/v1/evolution-decisions/{decision_id}` | Existing | Prior BFF work | Full decision with follow-through evidence |
| EVO-dispatch | `dispatched_at`, `execution_result` fields in EV-02 | **GAP** | LOOP-AUTO-EVO-005 | EVO-005 must prove these fields are populated from runtime-manager |

**Query sequence for drill 2:**
```
GET /api/v1/runtimes/{runtime_id}/status                    # observe stale heartbeat
→ GET /api/v1/telemetry/{runtime_id}/summary               # confirm anomaly signal
→ GET /api/v1/incidents?runtime_id={runtime_id}            # confirm incident auto-opened
→ GET /api/v1/incidents/{incident_id}                      # check reconciliation links
→ GET /api/v1/evolution-decisions?incident_id={incident_id} # confirm proposal created
→ GET /api/v1/evolution-decisions/{decision_id}            # check follow-through stage
```

**Pre-condition**: LOOP-AUTO-DEP-004 and LOOP-AUTO-EVO-005 and LOOP-AUTO-TEL-005 must be merged and deployed before this drill can produce verified evidence.

### 3.3 Summary of BFF Query Gaps

| Gap | Route | Blocking drill | Blocking task |
|---|---|---|---|
| SourceHealth connector view | `GET /api/v1/personas/{persona_id}/source-health` | Drill 1 | LOOP-AUTO-SRC-004 |
| Source connector list | `GET /api/v1/source-connectors` | Drill 1 | LOOP-AUTO-SRC-004 |
| Loop health read model (list) | `GET /api/v1/loops` | Drill 1 | LOOP-AUTO-BFF-001 |
| Loop health read model (detail) | `GET /api/v1/loops/{loop_id}` | Drill 1 | LOOP-AUTO-BFF-001 |
| Truth label fields | `truth_source_label` in source/persona surfaces | Drill 1 | LOOP-AUTO-BFF-003 |
| Deployment stage split | Stage fields in runtime/deployment surfaces | Drill 2 | LOOP-AUTO-DEP-004 |
| Evolution follow-through fields | `dispatched_at`, `execution_result` in EV-02 | Drill 2 | LOOP-AUTO-EVO-005 |

---

## 4. Operator Journey Specifications

### 4.1 Drill 1 — Source-to-Health Operator Journey

```
STEP 1: Open loop inventory panel
  Action:  GET /api/v1/loops?loop_id=source_ingestion
  Verify:  loop record shows current_maturity, controller_health, last_success_at, last_failure_at
  Pass if: maturity is "scheduled" or above, controller_health is not "unknown"
  Fail if: maturity is "api-only" or truth_source_label is "seed"/"fixture"

STEP 2: Open source connector panel
  Action:  GET /api/v1/source-connectors
  Verify:  Each connector shows connector_id, dataset_id, cadence, last_fetch_at, last_push_at, failure_reason
  Pass if: At least one connector has a non-null last_fetch_at and truth_source_label = "live" or "scheduled"
  Fail if: All connectors show seed/fixture labels; or last_fetch_at is null for all

STEP 3: Open persona source-health panel
  Action:  GET /api/v1/personas/{persona_id}/source-health
  Verify:  Shows per-source connector + schedule status, last fetch/push, failure_reason
  Pass if: Labels distinguish "live", "scheduled", "registry" from "seed"/"fixture"
  Fail if: Static source labels appear as live health; no distinction visible

STEP 4: Record evidence
  File:    docs/deployment/evidence/LOOP-AUTO-BFF-004-drill1-source-health.md
  Content: curl output for each step, maturity observed, truth labels observed
```

### 4.2 Drill 2 — Runtime-to-Incident-to-Evolution-Proposal Operator Journey

```
STEP 1: Observe runtime status
  Action:  GET /api/v1/runtimes/{runtime_id}/status
  Verify:  Shows heartbeat_at, deployment_mode, binding_id, stage breakdown
           (stage breakdown: approval / plan / saga / binding / runtime_fleet each labeled)
  Pass if: heartbeat_at is within expected freshness window, stage breakdown is visible
  Fail if: stage shows single "green" without breakdown; or heartbeat_at is null

STEP 2: Confirm telemetry anomaly surfaced
  Action:  GET /api/v1/telemetry/{runtime_id}/summary (use replay corpus from TEL-005)
  Verify:  Anomaly signal (heartbeat_loss or order_rejection_spike) appears in summary
  Pass if: telemetry record links runtime_id and event_type in summary
  Fail if: no anomaly signal; or telemetry summary is unavailable

STEP 3: Confirm incident auto-opened
  Action:  GET /api/v1/incidents?runtime_id={runtime_id}&status=open
  Verify:  Incident record exists with severity, trigger_source, reconciliation_ids
  Pass if: incident opened within expected propagation window after anomaly
  Fail if: no incident; or "no incidents" shown when service is unreachable (violates §3.4 of DEGRADED_OPERATOR_PATH)

STEP 4: Trace incident to postmortem
  Action:  GET /api/v1/incidents/{incident_id}
  Verify:  incident.postmortem_draft_id or postmortem link is present after resolution
  Pass if: postmortem_draft_id is populated once incident moves to resolved
  Fail if: postmortem link missing; postmortem surface returns empty

STEP 5: Trace postmortem to evolution proposal
  Action:  GET /api/v1/evolution-decisions?incident_id={incident_id}
  Verify:  Evolution decision record exists with status=proposed, action_type populated
  Pass if: exactly one decision per incident+target cluster
  Fail if: duplicate decisions; or decision missing when postmortem is published

STEP 6: Trace evolution proposal follow-through
  Action:  GET /api/v1/evolution-decisions/{decision_id}
  Verify:  proposed → reviewed → approved → dispatched → executed stages visible
           dispatched_at, execution_result, blocked_reason are populated
  Pass if: decision shows final stage with follow-through evidence
  Fail if: decision stuck at proposed with no stage progression; dispatched_at null

STEP 7: Record evidence
  File:    docs/deployment/evidence/LOOP-AUTO-BFF-004-drill2-runtime-incident-evolution.md
  Content: curl output for steps 1–6, timing gaps, anomaly → incident → evolution latency
```

---

## 5. Frontend Handoff Materials

This section describes what the operator frontend (Lovable / console) needs before the drills can be run through the UI. The drills may also be run via curl or CLI; UI is not required for evidence, but UI rendering bugs should be noted.

### 5.1 Required Panel Readiness

| Panel | Depends on BFF surface | Ready when | Drill |
|---|---|---|---|
| Loop Inventory | `GET /api/v1/loops` (LH-01, LH-02) | LOOP-AUTO-BFF-001 merged | Both |
| Source Connector Health | `GET /api/v1/source-connectors` | LOOP-AUTO-SRC-004 merged | Drill 1 |
| Persona Source Health | `GET /api/v1/personas/{id}/source-health` | LOOP-AUTO-SRC-004 merged | Drill 1 |
| Runtime Board (with stage split) | RT-01/RT-03 with stage breakdown | LOOP-AUTO-DEP-004 merged | Drill 2 |
| Telemetry Summary | TL-02 | Prior BFF work | Drill 2 |
| Incident Board | IN-01, IN-02 | Prior BFF work | Drill 2 |
| Evolution Decisions | EV-01, EV-02 with follow-through | LOOP-AUTO-EVO-005 merged | Drill 2 |

### 5.2 Truth Label Requirements

The operator frontend must never render a `truth_source_label` value of `"seed"` or `"fixture"` without a prominent visual marker. The canonical rule (from LOOP-AUTO-BFF-003) is:

- `"live"` → green label, no warning
- `"scheduled"` → amber label, last-success-at shown
- `"registry"` → grey label, "registered but not yet live"
- `"snapshot"` → blue label, "point-in-time snapshot"
- `"seed"` → red label, "SEED DATA — not live truth"
- `"fixture"` → red label, "FIXTURE — not live truth"

Panels that omit the label or show all data in the same visual style will fail the drill review.

### 5.3 Degraded State Display Requirements

Per `DEGRADED_OPERATOR_PATH.md §3.4` (Never Show None rule), the frontend must:

- Never show `"data": []` or empty list when the underlying service is unreachable
- Show `"data unavailable"` with `last_check_at` timestamp when Tier 5 degradation applies
- Show explicit `"status unknown"` for kill-switch status when runtime-manager is unreachable
- Show `"incident data unavailable"` — never "no incidents" — when incident service is unreachable
- Show `"binding state unverifiable"` when binding service is down (never "no bindings")

### 5.4 Composed Views Needed for Drills

These are not new surfaces but composed payloads that may require frontend assembly:

**Cross-loop summary view (optional, for drill closeout)**:
- Combines: loop maturity from LH-01 + runtime health from RT-03 + incident summary from IN-01 + evolution decision count from EV-01
- Purpose: single-screen confirmation that the cross-loop path is observable
- Not required for evidence; helpful for operator UX review

---

## 6. Acceptance Checklist

These criteria are derived from the canonical task record in `ai-status.json` and must all be satisfied before Claude2 may submit LOOP-AUTO-BFF-004 for review.

### 6.1 Drill 1 Acceptance

- [ ] **AC-D1-1 — All dependency BFF surfaces are deployed**
  - LOOP-AUTO-SRC-004 (SourceHealth), LOOP-AUTO-BFF-001 (loop read model), and LOOP-AUTO-BFF-003 (truth labels) must be merged to `dev` and confirmed deployed to the test environment before drill 1 runs.

- [ ] **AC-D1-2 — Source-to-health query sequence completes without error**
  - All four steps in Section 4.1 complete with HTTP 200 and non-empty payloads.
  - No step returns `[]` or `null` for an active source connector.

- [ ] **AC-D1-3 — Truth labels are visible and non-seed**
  - At least one source shows `truth_source_label` in `{live, scheduled, registry}`.
  - No source shows `seed` or `fixture` without a visible warning label.

- [ ] **AC-D1-4 — Loop maturity is not api-only**
  - The `source_ingestion` loop in the loop health read model shows `current_maturity` ≥ `scheduled`.

- [ ] **AC-D1-5 — File-backed evidence is written**
  - Evidence file: `docs/deployment/evidence/LOOP-AUTO-BFF-004-drill1-source-health.md`
  - Content: command output, maturity observed, labels observed, any failure reasons.

### 6.2 Drill 2 Acceptance

- [ ] **AC-D2-1 — All dependency BFF surfaces are deployed**
  - LOOP-AUTO-DEP-004 (stage split), LOOP-AUTO-TEL-005 (replay corpus), and LOOP-AUTO-EVO-005 (follow-through) must be merged to `dev` and confirmed deployed.

- [ ] **AC-D2-2 — Runtime status shows stage breakdown**
  - `GET /api/v1/runtimes/{runtime_id}/status` returns separate approval/plan/saga/binding/runtime_fleet fields.
  - A single aggregated "green" without breakdown fails this criterion.

- [ ] **AC-D2-3 — Anomaly flows to incident**
  - Using the replay corpus from LOOP-AUTO-TEL-005, a triggered anomaly (heartbeat_loss or order_rejection_spike) results in an incident record within the expected propagation window.
  - Confirmed via `GET /api/v1/incidents?runtime_id={runtime_id}`.

- [ ] **AC-D2-4 — Incident flows to evolution proposal**
  - A resolved incident results in exactly one evolution decision proposal per incident+target cluster.
  - Confirmed via `GET /api/v1/evolution-decisions?incident_id={incident_id}`.

- [ ] **AC-D2-5 — Evolution decision shows follow-through stages**
  - `GET /api/v1/evolution-decisions/{decision_id}` shows `dispatched_at` and `execution_result` (or `blocked_reason`) fields populated by LOOP-AUTO-EVO-005 evidence.

- [ ] **AC-D2-6 — File-backed evidence is written**
  - Evidence file: `docs/deployment/evidence/LOOP-AUTO-BFF-004-drill2-runtime-incident-evolution.md`
  - Content: command output for each step, propagation timing, anomaly source, final evolution stage.

### 6.3 Non-Goals

- Live-capital execution — drills use paper or canary runtimes; no real capital
- Approval gate bypass — evolution decisions must still require human approval gate
- Panel-only closure — screenshot without file-backed evidence is rejected
- Seed fixture as live proof — no seed connector satisfies drill 1 source-health requirement
- Simultaneous wave closeout — BFF-004 does not close any wave; it proves the drills ran

---

## 7. Maturity Gate

LOOP-AUTO-BFF-004 must reach `proven-live` maturity from `reconciled`. The difference:

| Maturity level | Requirement |
|---|---|
| api-only | API routes exist |
| scheduled | Scheduled workers run |
| reconciled (current) | Desired-state / actual-state query is live and auto-repairs |
| **proven-live (target)** | End-to-end evidence across restart, kill, and replay; both cross-loop drills pass |

The closeout evidence must state which maturity level was reached and list any criteria that remain unmet (remaining blockers).

---

## 8. Handoff Notes to Parent Owner (Claude2)

When Claude2 picks up LOOP-AUTO-BFF-004, the following pre-conditions should be verified:

1. **Confirm dependency task merge status**: All seven direct dependencies must be merged to `dev` before both drills can run. Check each one:
   - LOOP-AUTO-SRC-004 — SourceHealth connector surface in BFF
   - LOOP-AUTO-RT-005 — Runtime fleet evidence packet
   - LOOP-AUTO-DEP-004 — Stage-split deployment BFF truth
   - LOOP-AUTO-TEL-005 — Telemetry incident replay corpus
   - LOOP-AUTO-EVO-005 — Evolution follow-through evidence
   - LOOP-AUTO-KNOW-006 — Consultation workflow executor
   - LOOP-AUTO-BFF-003 — Truth label rendering in panels

2. **Use the replay corpus from TEL-005** for drill 2. Do not create a new anomaly from scratch — the replay corpus exists to enable repeatable evidence.

3. **Write evidence files before requesting review**: Evidence files must be at `docs/deployment/evidence/LOOP-AUTO-BFF-004-drill1-source-health.md` and `docs/deployment/evidence/LOOP-AUTO-BFF-004-drill2-runtime-incident-evolution.md` with actual curl output.

4. **The degraded-path panel behavior counts**: If any drill step returns an empty list when a service is unreachable, that is a panel bug (violates DEGRADED_OPERATOR_PATH §3.4) and should be filed as a blocker on the relevant dependency task, not hidden.

5. **Cross-loop composed view is optional**: A combined dashboard view is not required for evidence. File-backed CLI output is sufficient for the drills.

6. **Maturity statement in closeout**: The final evidence packet must include a one-line maturity statement: "LOOP-AUTO-BFF-004 reached `proven-live` maturity on YYYY-MM-DD" or "LOOP-AUTO-BFF-004 reached `reconciled` maturity; remaining blockers: [list]".

---

## 9. Reviewer Guidance (for Claude2 reviewing the handoff packet / Codex reviewing the parent task)

**Claude2 reviewing this sidecar packet:**
- Verify that the BFF query gaps in Section 3 match the actual BFF surface inventory (`services/control-plane/bff/BFF_SURFACE_INVENTORY.md` and `BFF_API_CONTRACT.md`).
- If any gap is already implemented, mark it as "implemented" with the implementing commit or PR.
- If any gap is missing from the listed dependency tasks, flag it as a new blocker before LOOP-AUTO-BFF-004 can run.

**Codex reviewing the parent task (LOOP-AUTO-BFF-004):**
- Each AC in Section 6 must have a corresponding line in the evidence files.
- The truth label check (AC-D1-3) must be confirmed from the actual BFF response payload, not from a panel screenshot.
- The stage breakdown check (AC-D2-2) must be confirmed from the actual BFF response payload showing separate stage fields.
- The maturity statement at closeout must be explicit and cite which evidence file supports it.

---

## 10. Sidecar Scope Constraints

This packet is a support artifact only. It:

- Does **not** modify any L1 canonical policy file
- Does **not** modify `ai-status.json`, `current-work.md`, or any loop registry
- Does **not** implement any BFF route, runtime logic, or evidence collection
- Does **not** change the parent task's acceptance criteria (it only documents them for handoff)
- May be updated by Claude2 (parent owner) or Codex (reviewer) as drills are executed
- Must be absorbed into the parent task's final evidence packet at LOOP-AUTO-BFF-004 closeout

---

## 11. Change Log

| Date | Author | Change |
|---|---|---|
| 2026-06-27 | Claude | Initial BFF handoff packet created (sidecar dispatch owned_ready_dispatch) |
