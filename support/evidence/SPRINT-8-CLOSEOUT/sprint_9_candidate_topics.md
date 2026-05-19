# Sprint 9 Candidate Topics

**Generated:** 2026-05-19
**Task-ID:** SPRINT-8-CLOSEOUT
**Owner:** Claude (LLM-Agent)
**Purpose:** Raw candidate list for Sprint 9 planning. These are topics for discussion,
not committed tasks. Planning session must validate dependencies, assign owners, and
set acceptance criteria before scheduling.

---

## Fail-Closed Reminder (Non-Negotiable)

The following constraints from prior sprints carry forward into Sprint 9 unchanged:

- **Broker-live remains forbidden.** No live broker side effects without risk-owner +
  operator dual gate. MGMT-BROKER-002 is still blocked on Shioaji credentials.
  M7 canary readiness is not closed.
- **Capital-binding-live remains forbidden.** Do not schedule tasks that write live
  capital positions until MGMT-BROKER-002 is explicitly unblocked and the canary
  approval gate is established.
- All paper → canary → live stage advances must pass pool/runtime compatibility check
  (DEP-004) once implemented.
- OSS integration evidence must land in `support/evidence/<epic>-<task>/` before any
  artifact is considered registry-admission-ready.

---

## Theme 1: OSS Production-Scale Completion

**Rationale:** Sprint 8 delivered only OSS-QLIB-V2-001 (in review). Four OSS V2
tasks remain todo: RLlib PPO, FinRL DRL, statsmodels cointegration, QuantLib option
chain. These tasks provide the empirical backbone for OODA loop evaluation and registry
admission. Without at least 3 completed OSS production runs, the OODA E2E proof chain
cannot assemble a realistic `OodaLoopPacket`. Sprint 9 should prioritize completing
all 4 remaining OSS V2 tasks before advancing OODA E2E integration.

**Candidate tasks:**
- OSS-RLLIB-V2-001 (Claude, reviewer Codex)
- OSS-FINRL-V2-001 (Gemini2, reviewer Codex2)
- OSS-STAT-V2-001 (Copilot, reviewer Codex)
- OSS-QUANTLIB-V2-001 (Copilot, reviewer Codex2)

**Load note:** Copilot owns 2 of these. Sprint 9 planning must check if Copilot
capacity allows both, or whether one should be reassigned.

---

## Theme 2: OODA E2E Dependency Resolution + First 3 Transitions

**Rationale:** Sprint 8 scheduled 5 OODA E2E tasks but made zero progress because
the dependency graph was not fully resolved first. Two specific blockers must be
addressed before OODA E2E can start: (a) DEP-004 pool/runtime compat check must
be implemented (it blocks OODA-E2E-004), and (b) OODA-E2E-005 and OODA-E2E-006
task definitions are missing from ai-status.json (they block OODA-E2E-007).

Sprint 9 should resolve the dependency gaps first, then run only the first 3
transition tests (OODA-E2E-001, 002, 003) that have clean dependency graphs.
OODA-E2E-004 and OODA-E2E-007 should be deferred until DEP-004 and the
005/006 definitions are in place.

**Candidate tasks:**
- DEP-004 (Codex, reviewer Codex2) — prerequisite
- Define and schedule OODA-E2E-005 and OODA-E2E-006 — planning session work
- OODA-E2E-001 (Codex, reviewer Codex2)
- OODA-E2E-002 (Codex2, reviewer Codex)
- OODA-E2E-003 (Claude, reviewer Codex)

**Load note:** Codex would hold DEP-004 + OODA-E2E-001 simultaneously. Sprint 9
planning should serialize these or split them across agents to keep load < 2 per agent.

---

## Theme 3: Governance and Evolution Feedback Loop Closure

**Rationale:** POST-EVO-BRIDGE (postmortem → EvolutionDecisionProposal auto-trigger)
was scheduled in Sprint 8 under Claude2 but not started. This bridge completes the
learning arm of the OODA loop: incidents produce postmortems, postmortems trigger
evolution proposals, evolution decisions feed back into the deployment pipeline.
Without this bridge, the OODA loop has a manual gap between `learn` and `decide`
that undermines the automation promise.

Sprint 9 should also check whether GOV-001 (ApprovalDecision first-class service)
is stable enough to support OODA-E2E-004, and whether EVOLUTION-COOLDOWN policy
integration with the bridge is needed before registry admission can gate on evolution
cooldown windows.

**Candidate tasks:**
- POST-EVO-BRIDGE (Claude2, reviewer Codex2)
- Governance stability review (Claude, reviewer Codex) — confirm GOV-001, DEP-001..003
  are stable before OODA-E2E-004 is unblocked.

---

## Theme 4: Agent Utilization and Load Balancing Process Fix

**Rationale:** Sprint 8 ended with Claude2, Gemini, and Gemini2 in `ready` state
having made zero progress on their assigned `todo` tasks. The sprint had 18 tasks
across 7 agents in a 2-day window with no enforcement of per-agent load caps. This
produced concentrated delivery on Codex while other agents idled.

Sprint 9 planning session should adopt a formal load rule (max 2 active tasks per
agent per sprint), define escalation triggers for idle agents (e.g., after 12h
without progress on a `todo` task, the supervisor must reassign or dispatch a wake-up),
and reduce total concurrent tasks to a realistic throughput target for the sprint length.

**Candidate tasks:**
- Update `.orchestrator/state.json` or supervisor config to enforce max-tasks-per-agent cap.
- Add idle-agent escalation hook to supervisor dispatch loop.
- Reduce Sprint 9 initial task list to ≤ 2 per agent.

**Note:** This is a process/config change, not an execution task. It may be handled
as a chair-review action or a dedicated ops task rather than a normal implementation task.

---

## Theme 5: Strategy Spec and Experiment Deep Production

**Rationale:** STRAT-V2-001 (production distillation from real research notes) and
EXP-V2-001 (parallel multi-backend dispatch) are the production-scale counterparts
of the strategy and experiment services. They were not started in Sprint 8 due to
agent capacity. These tasks are not on the critical path for OODA E2E (which can
use fixture data), but they demonstrate that the research pipeline produces real
artifacts from real data. EXP-V2-002 (multi-artifact lineage tree) provides the
lineage infrastructure needed for OSS V2 admission packets to carry complete provenance.

Sprint 9 should schedule these after OSS production runs are complete, since the
parallel dispatch (EXP-V2-001) is most useful when at least 3 OSS adapters have
production-validated results to compare.

**Candidate tasks:**
- STRAT-V2-001 (Copilot, reviewer Codex2)
- EXP-V2-001 (Codex, reviewer Codex2) — after OSS V2 is done
- EXP-V2-002 (Codex2, reviewer Copilot)

---

## Topic Prioritization Summary

| Priority | Theme | Key Deliverable | Prerequisite |
|---|---|---|---|
| P0 | OSS Production-Scale Completion | 4 OSS V2 production runs complete | OSS-QLIB-V2-001 review closes |
| P0 | OODA E2E Dependency Resolution | DEP-004 done; OODA-E2E-005/006 defined | None |
| P1 | Governance/Evolution Feedback | POST-EVO-BRIDGE done | POST-001, EVO-001 stable |
| P1 | OODA E2E First 3 Transitions | OODA-E2E-001/002/003 passing | DEP-004 done |
| P2 | Process: Load Cap Enforcement | Supervisor max-tasks-per-agent cap | Chair review |
| P3 | Strategy/Experiment Deep | STRAT-V2-001, EXP-V2-001, EXP-V2-002 | OSS V2 complete |

---

*This is a candidate list for discussion in the Sprint 9 planning session.
No tasks should be materialized in ai-status.json until the planning session reaches
consensus and human approval is obtained (per discussion_planning protocol).*
