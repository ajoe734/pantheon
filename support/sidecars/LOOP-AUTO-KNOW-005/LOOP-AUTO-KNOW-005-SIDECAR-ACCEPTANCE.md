# Acceptance Packet: LOOP-AUTO-KNOW-005
# Add human imitation and shadow evaluation scheduler

**Sidecar kind:** acceptance_packet
**Parent task:** LOOP-AUTO-KNOW-005
**Sidecar task:** LOOP-AUTO-KNOW-005-SIDECAR-ACCEPTANCE
**Prepared by:** Claude2
**Date:** 2026-06-27
**Reviewer:** Claude

---

## 1. Task Identity

| Field | Value |
|---|---|
| Task ID | LOOP-AUTO-KNOW-005 |
| Title | Add human imitation and shadow evaluation scheduler |
| Phase | Global Loop Autopilot / Wave 6 Knowledge Learning Consultation |
| Owner | Gemini |
| Reviewer | Codex |
| Status | todo |
| Loop ID | human_imitation_shadow_evaluation |
| Current maturity | api-only |
| Target maturity | scheduled |
| Wave | Wave 6 Knowledge Learning Consultation |

---

## 2. Dependency Map

### Direct Dependencies

| Task ID | Title | Status | Owner | Why needed |
|---|---|---|---|---|
| LOOP-AUTO-KNOW-004 | Extract Agora interaction evidence into datasets | todo | Copilot | Produces learning datasets that serve as trace inputs for imitation/shadow eval |
| LOOP-AUTO-TEL-005 | Add telemetry incident replay and operator evidence | todo | Gemini2 | Provides telemetry replay corpus that feeds imitation/shadow eval runs |

### Upstream Dependency Chain

```
LOOP-AUTO-000  (loop catalog schema + maturity registry)
  └─ LOOP-AUTO-SRC-001  (persona data requirement schema)
       └─ LOOP-AUTO-SRC-002  (source provisioning reconciler)
            └─ LOOP-AUTO-SRC-003  (source scheduler supervision)
                 └─ LOOP-AUTO-SRC-004  (SourceHealth into persona panels)
                      └─ LOOP-AUTO-SRC-005  (source completion → search refresh)
                           └─ LOOP-AUTO-KNOW-001  (source-to-strategy distillation worker)
                                └─ LOOP-AUTO-KNOW-002  (alpha replication queue + revalidation)
                                     └─ LOOP-AUTO-KNOW-003  (persona teaching eval worker)
                                          └─ LOOP-AUTO-KNOW-004  (Agora evidence → datasets)
                                               └─ LOOP-AUTO-KNOW-005  ← THIS TASK

LOOP-AUTO-TEL-001  (telemetry readiness + writer durability)
  └─ LOOP-AUTO-TEL-002  (scheduled reconciliation worker)
       └─ LOOP-AUTO-TEL-003  (incident-triggered reconciliation)
            └─ LOOP-AUTO-TEL-004  (drift → incident with dedupe)
                 └─ LOOP-AUTO-TEL-005  (telemetry incident replay + operator evidence)
                      └─ LOOP-AUTO-KNOW-005  ← THIS TASK
```

### Downstream Dependents

No tasks directly depend on LOOP-AUTO-KNOW-005. This task is a leaf node in Wave 6; its outputs feed the Wave 7 cross-loop operator drills (LOOP-AUTO-BFF-004) indirectly through the overall knowledge loop readiness.

---

## 3. Acceptance Checklist

These criteria are derived from the canonical task record in `ai-status.json` and must all be satisfied before Gemini may request review.

### 3.1 Functional Acceptance

- [ ] **AC-1 — Scheduled imitation/shadow eval runs from trace datasets**
  - Trace dataset records produced by LOOP-AUTO-KNOW-004 (Agora evidence) and LOOP-AUTO-TEL-005 (telemetry replay) trigger imitation or shadow evaluation jobs on schedule.
  - Scheduled jobs run without a manual POST or human trigger.
  - Evidence: scheduler worker last-success timestamp and job records present in services/policy-learning or services/research-worker-gateway.

- [ ] **AC-2 — Candidates require experiment approval and deployment gates**
  - ShadowImitationCandidate records produced by the scheduler are gated: they cannot advance to any deployment path without an explicit ExperimentRun approval.
  - The deployment gate must exist in the code path, not only in policy documentation.
  - Evidence: unit test or integration test showing a candidate blocked without approval; code path reference.

- [ ] **AC-3 — Production training remains fail-closed until explicitly activated**
  - No production-affecting training action is triggered by default.
  - The fail-closed default is enforced in code (e.g., a feature flag, an approval state check, or a gated adapter call) — not only in documentation.
  - Evidence: test proving fail-closed behavior; code reference to the enforcement point.

### 3.2 Operational Acceptance

- [ ] **AC-4 — Scheduler is supervised (required worker, not optional)**
  - The imitation/shadow eval scheduler appears in docker-compose.yml or equivalent supervisor manifest as a required service, not an optional profile.
  - Worker exposes health metrics: last_success_at, last_failure_at, job_count, missed_tick count.

- [ ] **AC-5 — Duplicate ticks do not create duplicate eval records**
  - Idempotency is tested: replaying the same tick or dataset record does not produce duplicate ShadowImitationCandidate or ExperimentRun records.
  - Evidence: unit test or integration test demonstrating idempotency.

- [ ] **AC-6 — Desired-state / actual-state reconciliation is observable**
  - Operator can query the BFF or a read model for the current desired state (learning datasets queued) vs. actual state (eval runs completed, candidates produced).
  - This satisfies the "operator-visible truth projection required" dispatch rule.

### 3.3 Evidence Requirements

All evidence must be file-backed in `docs/deployment/evidence/` or the task artifact paths before the task can move to `review`. Panel-only screenshots do not count.

| Evidence item | Acceptable format |
|---|---|
| Scheduler worker supervision | docker-compose.yml diff or manifest entry with health check |
| Scheduled job execution | Log snippet or test output showing job ran from schedule |
| Gate enforcement (AC-2) | Unit or integration test file path + test name |
| Fail-closed enforcement (AC-3) | Test file path + test name |
| Idempotency (AC-5) | Test file path + test name |
| Operator read model (AC-6) | BFF endpoint or read model file path |

### 3.4 Non-Goals (must not be present in the deliverable)

- Live-capital execution — no real money moved during eval or training
- Approval gate bypass — candidates never skip the experiment-approval gate
- Panel-only closure — dashboard green without file-backed evidence is rejected
- Seed fixture as live proof — seed or fixture data does not satisfy the evidence requirement
- Direct production mutation — no running runtime is modified by this scheduler

---

## 4. Maturity Gate

LOOP-AUTO-KNOW-005 must reach `scheduled` maturity, meaning:

| Maturity level | Requirement |
|---|---|
| api-only (current) | API routes exist; scheduler not wired |
| **scheduled (target)** | Scheduler runs from timer; jobs are recorded; supervision is configured |
| reconciled (future) | Desired-state / actual-state query is live and auto-repairs drift |
| proven-live (future) | End-to-end evidence across restart, kill, and replay |

The task closeout must not claim a maturity level above `scheduled` unless additional evidence is collected.

---

## 5. Handoff Notes to Parent Owner (Gemini)

When Gemini picks up LOOP-AUTO-KNOW-005, the following pre-conditions should be verified before beginning implementation:

1. **LOOP-AUTO-KNOW-004 is sufficiently advanced** — the `AgoraInteractionEvidence` → learning dataset pipeline must produce records that the imitation scheduler can consume. If LOOP-AUTO-KNOW-004 is still api-only, the scheduler has no real input to test against.

2. **LOOP-AUTO-TEL-005 evidence corpus is accessible** — the telemetry replay corpus from LOOP-AUTO-TEL-005 is the second input. Even a partial corpus is acceptable for initial scheduled runs.

3. **services/policy-learning exists or must be created** — the task artifacts reference `services/policy-learning`. If this service does not yet exist, Gemini must create the skeleton before wiring the scheduler.

4. **research-worker-gateway gateway contract is stable** — if the scheduler dispatches through `services/research-worker-gateway`, verify the gateway accepts imitation/shadow eval job types before wiring.

5. **Fail-closed is the default** — start with the adapter returning a no-op or a gated-pending state. Activating production training is an explicit out-of-scope follow-up, not a deliverable of this task.

---

## 6. Reviewer Guidance (for Codex reviewing LOOP-AUTO-KNOW-005)

When reviewing the parent task deliverable, Codex should verify:

- Each AC (3.1 above) has a corresponding file-backed evidence item.
- The scheduler is listed in the supervisor manifest as `restart: always` or equivalent — not commented out, not optional-profile only.
- The gate enforcement (AC-2, AC-3) is code-level, not policy-doc-level.
- Idempotency is tested, not assumed.
- No `mutates_canonical: true` fields in this task were modified outside approved scope — this sidecar packet must not touch canonical truth files.

---

## 7. Sidecar Scope Constraints

This packet is a support artifact only. It:

- Does **not** modify any L1 canonical policy file
- Does **not** modify `ai-status.json`, `current-work.md`, or any loop registry
- Does **not** implement any runtime or governance logic
- May be updated by the parent owner (Gemini) or the reviewer (Codex) as implementation progresses
- Must be absorbed into the parent task's final evidence packet at LOOP-AUTO-KNOW-005 closeout

---

## 8. Change Log

| Date | Author | Change |
|---|---|---|
| 2026-06-27 | Claude2 | Initial acceptance packet created (sidecar dispatch owned_ready_dispatch) |
