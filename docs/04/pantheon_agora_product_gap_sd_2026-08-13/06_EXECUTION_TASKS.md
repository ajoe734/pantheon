# Agora Governed Execution Tasks

Status: task catalog prepared for local Human/Ops canonical materialization

Canonical machine-readable catalog:
[execution-tasks.json](execution-tasks.json)

Materialization ID: `local-human-ops-agora-product-gap-sd-20260813-v1`

The earlier `pkt-agora-product-gap-sd-20260813-v1` attempt was rejected before
materialization and created no task rows. The planner had inspected the stale
mutable `pantheon-ci-deploy/dev-root` bridge instead of the live immutable
command runtime. The authoritative runtime already contains
`OPS-LOCAL-HUMAN-OPS-CANONICAL-20260812`, so task creation uses
`scripts/human-ops-status.sh` and does not depend on product BFF login, control
mode, bearer tokens, or bridge signing.

## Materialization boundary

These are canonical task specifications for supervisor/auto-worker execution.
The planner does not implement their product scope. They are materialized
through the explicit local Human/Ops canonical CLI; queue/state JSON must not
be edited by hand.

The first task independently reviews and merges PR #4834. It is a narrow plan
gate, not a product-code gate. The source catalog deliberately reuses three
already-materialized L12 tasks instead of creating duplicates:

- `L12-MFC-R4-AGORA-001`: durable Agora handoff → policy readback → ACK;
- `L12-MFC-R4-IMITATION-001`: processed candidate → Research experiment;
- `L12-MFC-R4-CONSULT-001`: submitted-only intake → real Consultation workflow.

Because `L12-MFC-R4-AGORA-001` declares the parent scope
`services/control-plane/bff/agora`, new child-scope BFF tasks must wait for it.
That ordering is conflict prevention, not a product dependency. Policy-learning
files outside its scope and all four `execute-plans` tasks can proceed after the
Agora plan merge without waiting for that parent-scope task.

## Parallel execution topology

```text
AGORA-PROD-PLAN-FREEZE-20260813
  ├─ execute-plans: Workshop UI ───────────────┐
  ├─ execute-plans: Candidate component ──────┤
  ├─ execute-plans: Workspace components ─────┼─> AGORA-FE-INTEGRATION
  ├─ execute-plans: Performance UI ───────────┘
  └─ Policy-learning admit/worker

existing L12-MFC-R4-PLAN-FREEZE-002
  ├─ existing L12-MFC-R4-AGORA-001 ───────────┬─ Workshop backend
  │                                            ├─ Research/candidate backend
  │                                            ├─ Trading authority/compiler
  │                                            ├─ Trading data/events
  │                                            ├─ Performance backend
  │                                            └─ Dataset admit-only follow-up
  ├─ existing L12-MFC-R4-IMITATION-001 ───────┐
  └─ existing L12-MFC-R4-CONSULT-001 ─────────┴─> AGORA-BE-INTEGRATION

AGORA-BE-INTEGRATION + AGORA-FE-INTEGRATION
  └─ AGORA-HOSTED-ACCEPTANCE-20260813
```

After the unavoidable parent-scope task, six backend domains run in parallel.
Backend and frontend integration also run in parallel. Only hosted deployment
waits for both.

## Task matrix

| Task | Repository | Owner | Reviewer | Direct dependencies |
|---|---|---|---|---|
| `AGORA-PROD-PLAN-FREEZE-20260813` | Pantheon | Claude | Antigravity | — |
| `AGORA-WORKSHOP-CORE-20260813` | Pantheon | Codex | Claude | plan freeze, existing L12 Agora |
| `AGORA-RESEARCH-CANDIDATE-20260813` | Pantheon | Codex2 | Claude2 | plan freeze, existing L12 Agora |
| `AGORA-TRADING-AUTH-20260813` | Pantheon | Claude | Codex2 | plan freeze, existing L12 Agora |
| `AGORA-TRADING-DATA-EVENTS-20260813` | Pantheon | Claude2 | Codex | plan freeze, existing L12 Agora |
| `AGORA-PERFORMANCE-INDEX-20260813` | Pantheon | Codex | Antigravity | plan freeze, existing L12 Agora |
| `AGORA-DATASET-ADMIT-ONLY-20260813` | Pantheon | Codex2 | Claude | plan freeze, existing L12 Agora |
| `AGORA-POLICY-ADMIT-WORKER-20260813` | Pantheon | Claude2 | Codex | plan freeze |
| `AGORA-FE-WORKSHOP-20260813` | execute-plans | Codex2 | Claude | plan freeze |
| `AGORA-FE-CANDIDATE-20260813` | execute-plans | Codex | Claude2 | plan freeze |
| `AGORA-FE-WORKSPACE-CLEANUP-20260813` | execute-plans | Claude | Codex2 | plan freeze |
| `AGORA-FE-PERFORMANCE-20260813` | execute-plans | Claude2 | Codex | plan freeze |
| `AGORA-BE-INTEGRATION-20260813` | Pantheon | Codex | Antigravity2 | seven new backend tasks plus reused Imitation/Consultation |
| `AGORA-FE-INTEGRATION-20260813` | execute-plans | Codex2 | Antigravity | four frontend and five serving-backend tasks |
| `AGORA-HOSTED-ACCEPTANCE-20260813` | Pantheon | Claude2 | Antigravity2 | both integration tasks |

## Global worker rules

- Start from latest remote `dev` in a supervisor-managed clean worktree.
- Pantheon and execute-plans changes stay in separate tasks and PRs.
- `execute-plans:*` artifacts mean paths in `/home/lupin/code/execute-plans`,
  never paths copied under Pantheon.
- Do not build on client-written completeness/readiness/freshness, read-role
  mutations, unscoped records, hard-coded live candidates, inline fake async,
  local-only UI success, or auto-approved Consultation.
- An implementation task may preserve sound primitives documented as KEEP; it
  must remove, quarantine, or replace the incorrect behavior in its GAP IDs.
- Every task needs its declared branch, focused tests, required trailers, push,
  PR to `dev`, visible checks, independent exact-head review, merge, and
  required evidence/readback.
- Verifier/integration tasks do not repair component failures. They return the
  failure to the owning task.
- No Supervisor V2 implementation, Lovable, legacy frontend repository,
  production deployment, broker order, live-capital action, or direct runtime
  state editing is in scope.
