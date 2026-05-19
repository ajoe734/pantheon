# Sprint 9 Candidate Topics

**Generated:** 2026-05-19
**Task-ID:** SPRINT-8-CLOSEOUT
**Owner:** Claude (LLM-Agent)
**Purpose:** Raw candidate list for Sprint 9 planning. These are topics for discussion,
not committed tasks. Planning session must validate dependencies, assign owners, and
set acceptance criteria before scheduling.

**Sprint 8 context:** All 20 Sprint 8 implementation tasks were completed. The OODA E2E
proof chain (paper stage), all 5 OSS framework production runs, full governance loop,
and strategy/experiment deep production are now done. Sprint 9 can advance to the next tier.

---

## Fail-Closed Reminder (Non-Negotiable)

The following constraints from prior sprints carry forward into Sprint 9 unchanged:

- **Broker-live remains forbidden.** No live broker side effects without risk-owner +
  operator dual gate. MGMT-BROKER-002 is still blocked on Shioaji credentials.
  M7 canary readiness is not closed.
- **Capital-binding-live remains forbidden.** Do not schedule tasks that write live
  capital positions until MGMT-BROKER-002 is explicitly unblocked and the canary
  approval gate is established.
- All paper → canary → live stage advances must pass DEP-004 pool/runtime compatibility
  check (now implemented and tested).
- OSS integration evidence must land in `support/evidence/<epic>-<task>/` before any
  artifact is considered registry-admission-ready.

---

## Theme 1: Canary Advancement Path (Gated on MGMT-BROKER-002)

**Rationale:** Sprint 8 proved the full OODA loop at paper stage. The natural next step
is canary advancement — deploying the first approved strategy artifact to a canary
runtime with real market data but no live capital binding. This requires:
(a) MGMT-BROKER-002 unblocked (Shioaji credentials obtained),
(b) the canary approval gate established (risk-owner + operator dual gate),
(c) a canary DeploymentPlan using the paper OODA artifacts from Sprint 8.

Sprint 9 planning should either confirm MGMT-BROKER-002 is unblocked (allowing canary
tasks to be scheduled) or explicitly defer all canary work to Sprint 10. There is no
half-canary state. The gate is binary.

**Candidate tasks (conditional on MGMT-BROKER-002 unblocked):**
- Canary approval gate implementation (Claude, reviewer Codex) — establish the dual-gate
  approval flow for paper → canary stage transition.
- First canary DeploymentPlan using Sprint 8 OODA artifacts (Codex, reviewer Claude).
- Canary runtime binding smoke test (Claude2, reviewer Codex2).

**Prerequisite gate check:** Sprint 9 planning session must confirm MGMT-BROKER-002
status before scheduling any of these tasks.

---

## Theme 2: Multi-Persona OODA Orchestration

**Rationale:** Sprint 8 proved OODA end-to-end for a single persona (paper stage). The
MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION policy defines how multiple personas
contribute to a shared StrategySpec pool and how conflicts are resolved. Sprint 9 can
implement the multi-persona synthesis layer: given N personas each contributing research
notes and strategy specs, the orchestrator should synthesize a sponsor-resolved allocation
proposal before the OODA Decide phase.

This does not require broker live or canary. It can run fully in paper mode with fixture
personas and addresses a key gap between single-persona proof (Sprint 8) and production
system capability (Sprint 10+).

**Candidate tasks:**
- Multi-persona sponsor resolution service (Claude, reviewer Codex) — implement the
  conflict resolution logic per MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md.
- Multi-persona OODA E2E test (Codex, reviewer Claude2) — extend the Sprint 8 OODA
  E2E test chain to include 2+ personas with a synthesis step.
- Persona registry admission gate (Codex2, reviewer Claude) — confirm persona registry
  health before synthesis.

---

## Theme 3: Registry Promotion Lifecycle Hardening

**Rationale:** Sprint 8 delivered registry admission packets (candidate state) for all
5 OSS frameworks. The full promotion state machine — candidate → approved → canary →
live — was partially proven via OODA-E2E-004 (candidate → approved → paper DeploymentPlan).
Sprint 9 should harden the promotion lifecycle: confirm promotion state transitions are
idempotent, evidence-gated, and auditable for the full lifecycle path up to (but not
including) canary live binding.

**Candidate tasks:**
- Registry promotion audit (Codex, reviewer Codex2) — trace the full state machine
  from Sprint 8 OSS admission packets through to approved state and confirm evidence chain.
- Promotion idempotency and rollback test (Claude, reviewer Codex) — confirm that
  repeat admission does not corrupt artifact state.
- Registry admission CLI/API hardening (Codex2, reviewer Claude) — expose a clean API
  for submitting and querying promotion state.

---

## Theme 4: Telemetry and Incident Response Production Hardening

**Rationale:** OODA-E2E-006 (telemetry → Incident → Postmortem → EvolutionDecisionProposal)
and POST-EVO-BRIDGE both completed in Sprint 8. The individual service contracts are
proven. Production hardening means: (a) the telemetry ingest pipeline handles realistic
message volumes without dropping events; (b) the incident → postmortem workflow enforces
the required evidence fields before an EvolutionDecisionProposal is emitted; (c) the
EVOLUTION_COOLDOWN policy is enforced between sequential proposals for the same artifact.

**Candidate tasks:**
- Telemetry ingest load test (Gemini, reviewer Gemini2) — validate the
  TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE shock-absorption layer under 10× normal load.
- EvolutionCooldown enforcement integration test (Claude2, reviewer Codex2) — confirm
  EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY gates are applied before a new proposal
  is emitted for an artifact still in cooldown.
- Incident severity escalation test (Codex, reviewer Claude) — confirm that a high-severity
  incident triggers postmortem creation and proposal emission within the SLA window.

---

## Theme 5: BFF Live Integration and Frontend Smoke Tests

**Rationale:** The EPIC-BFF-P0 delivered the core BFF endpoints (session, auth, strategies,
personas, capital-pools, audit) in prior sprints. LOVABLE-STRICT-PUBLISH (Sprint 8)
produced the audit infrastructure for verifying the execute-plans frontend uses strict
env. Sprint 9 should run a full BFF live integration smoke under VITE_BFF_FALLBACK=strict:
confirm that the frontend bootstrap flow (Management panel) completes without mock fallback
for all core endpoints.

**Candidate tasks:**
- BFF live bootstrap integration test (Gemini, reviewer Gemini2) — run execute-plans@main
  against the pantheon BFF with VITE_BFF_FALLBACK=strict and confirm zero fallback paths.
- BFF audit report generation (Codex, reviewer Claude) — produce a formal BFF readiness
  report covering all EPIC-BFF-P0 endpoints.
- Session pair and auth refresh production hardening (Claude2, reviewer Codex2) — confirm
  session/refresh lifecycle handles token expiry and concurrent refresh without race conditions.

---

## Theme 6: Process and Supervisory Infrastructure

**Rationale:** Sprint 8 produced 20 tasks in 4 days, with 14 completing on the final day.
The supervisory infrastructure handled the load, but the closeout tooling revealed a gap:
SPRINT-8-CLOSEOUT was dispatched before all sprint tasks finished, causing an early
retrospective that required a reviewer reopen. Sprint 9 should harden the sprint closeout
dispatch gate and improve mid-sprint visibility.

**Candidate tasks (process/config, may be chair-review actions):**
- Sprint closeout dispatch gate (Codex, reviewer Claude) — add a supervisor check that
  SPRINT-X-CLOSEOUT is not dispatched until ≥ 80% of sprint tasks are in `done` or
  `review_approved`.
- Mid-sprint task velocity dashboard (Gemini, reviewer Codex) — surface tasks_archived_today
  and tasks_in_review in the dashboard to give a forward-looking completion estimate.
- Worker idle escalation improvement — confirm the 12-hour idle escalation hook is active
  and properly triggers wake-up or reassignment. (Sprint 8 showed most tasks completing
  late rather than spreading across the sprint window.)

---

## Topic Prioritization Summary

| Priority | Theme | Key Deliverable | Prerequisite |
|---|---|---|---|
| P0 (gated) | Canary Advancement Path | First canary DeploymentPlan + gate | MGMT-BROKER-002 unblocked |
| P0 | Multi-Persona OODA | Multi-persona synthesis + E2E test | Sprint 8 paper OODA artifacts |
| P1 | Registry Promotion Hardening | Full lifecycle audit + idempotency tests | Sprint 8 OSS admission packets |
| P1 | Telemetry/Incident Hardening | Load test + cooldown enforcement + SLA test | OODA-E2E-006 + POST-EVO-BRIDGE done ✓ |
| P2 | BFF Live Integration | BFF strict-mode bootstrap smoke | EPIC-BFF-P0 done ✓ |
| P2 | Process/Supervisor Hardening | Dispatch gate + velocity dashboard | Chair-review |

---

*This is a candidate list for discussion in the Sprint 9 planning session.
No tasks should be materialized in ai-status.json until the planning session reaches
consensus and human approval is obtained (per discussion_planning protocol).*
