# Round 002 - Parallel contract-coverage audit (loops #1-#16)

- Date: 2026-06-14
- Method: 4 parallel sub-agents (user-requested parallelization), each owning a loop
  domain, comparing canonical design surface vs live dev openapi.json (443 routes) vs
  live probes. All claims below were RE-VERIFIED by direct curl before recording.
- Live dev BFF: https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io
- Branch: task/verify-r2-contract-audit (off dev incl. R001)

## Positive results (verified)

- No 5xx observed on any probed endpoint. The live surface is healthy.
- Protected reads are uniformly fail-closed: 401 AUTH_REQUIRED with structured i18n
  envelope (`/api/v1/{deployment-plans,evolution-decisions,kill-switch/status,
  freeze-orders,incidents,postmortems,capital-pools,consult/requests,bindings}`,
  `/bff/*`, `/bff/synthesis/conflict-logs`).
- `/readyz` deps all ok (runtime-manager:8081, governance:8082, deployment:8095).

## FALSIFIED sub-agent claim (caught by ground-truth)

- Claim: "deployment_mode enum is [paper, live]; canary missing (P0)".
  REALITY: code supports canary. `services/execution/runtime-manager/runtime_manager.py:532`
  uses `DeploymentMode.CANARY.value`; `runtime_binding.py:26,126` documents
  `paper / canary / live / frozen`; 161 service files reference canary.
  => No fix needed. Recorded to prevent a future wrong "fix". Sub-agent findings are
  leads, not facts; every actionable claim must be ground-truthed first.

## Verified gaps (escalated as findings, NOT auto-fixed)

These are real (confirmed 404), but they are FEATURE / DESIGN-LEVEL decisions, not config
drift. Auto-implementing API surface from a probe would be unsafe and possibly wrong, so
they are escalated rather than changed this round:

| Missing route (confirmed 404) | Loop | Note |
|---|---|---|
| `GET /api/v1/allocation/proposals` | #6 aggregation | `PersonaAllocationProposal` not exposed at v1; only `/bff/synthesis/conflict-logs` (401) exists |
| `GET /api/v1/allocation/artifacts` | #6 aggregation | `AllocationPolicyArtifact` read not exposed at v1 |
| `GET /api/v1/deployment-policies` | #12 promotion | thresholds (paper/canary/live gates) not queryable via API |
| `POST /api/v1/kill-switch/activate` | #14 kill-switch | no v1 mutation endpoint; design routes kill-switch via runtime-manager fast path / operator-commands |
| `/api/v1/evolution-decisions/{id}/{review,approve,execute}` | #13 evolution | only GET list/detail at v1; lifecycle mutation is command-driven |

By design (NOT gaps): runtime-binding writes are owned by runtime-manager (RUN-001), and
promotion/evolution/kill-switch mutations are command-driven (LOOP_TRIGGER §3.7), routed
through `/api/v1/operator/commands` + `/bff/*` action endpoints, not direct v1 mutators.

## Systemic low-severity finding (design decision, NOT swept this round)

`GET /api/v1/trainer/sessions` returns **422 (validation) before 401 (auth)** for an
unauthenticated caller, because `persona_id: str` is a required FastAPI query param
validated before the in-body auth check (`main.py:12388`). This is SYSTEMIC: ~81 GET
endpoints in `services/control-plane/bff/main.py` use required no-default query params and
share this 422-before-401 ordering. Path-param siblings (e.g. `/sessions/{id}`) correctly
return 401.

Impact: an unauthenticated caller can learn an endpoint exists and its required params
before auth. Low severity (read endpoints, no data leak). Fixing means moving auth ahead of
param validation across ~81 endpoints -- a broad, behavior-shifting refactor that is a
design decision, not a clear-cut bug. Escalated for an explicit fail-closed-ordering
decision rather than an autonomous sweep. No existing contract test covers the
unauth/missing-param GET-list case, so a future fix is test-safe to add.

## Decision

No autonomous code change this round: the substantive gaps are feature/design decisions and
the one systemic pattern is too broad to sweep safely. This audit IS the round deliverable
(verification + archive). Findings escalated above.

## Loop coverage matrix (design / API / actually-runs)

| Loop | design | API present | actually runs |
|------|:--:|:--:|:--:|
| #1 Source Ingestion | y | operator-scoped | unproven (token) |
| #2 Strategy Distillation | y | y (knowledge/strategy-specs) | unproven |
| #3 Alpha Replication | y | y (experiments) | unproven |
| #4 Persona Teaching | y | y (trainer/sessions) | unproven |
| #5 Human Imitation | y | y (agora training/eval) | unproven |
| #6 Consultation+Aggregation | y | partial (consult y; allocation 404) | unproven |
| #7 Promotion/Deployment | y | y (deployment-plans, bindings) | unproven |
| #8 Capital Pool Execution (LEAN) | y | indirect (runtime-bindings RO) | unproven |
| #9 Telemetry/Reconciliation | y | y (incidents, drift via telemetry) | unproven |
| #10 Evolution | y | partial (GET only at v1) | unproven |
| #11 BFF Health | y | y (/health,/readyz,/metrics live) | partial (deps ok) |
| #12 paper->canary->live | y | partial (policies 404) | unproven |
| #13 Evolution lifecycle | y | partial (mutation command-driven) | unproven |
| #14 Kill-switch/Safe-mode | y | partial (status RO; activate 404) | unproven |
| #15 Rollback/Position | y | RO; lineage internal to runtime-manager | unproven |
| #16 Delivery closure | y | n/a (process loop, not BFF) | n/a |
