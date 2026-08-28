# Pantheon Architecture Cleanup Execution DAG — 2026-08-28

Status: **ready for independent plan-freeze review and supervisor materialization**

Program: `PANTHEON-ARCH-CLEANUP-20260828`

This document converts the approved code-first GAP/SA/SD into executable task
ownership. It does not change product behavior. The exact task contracts are in
[`EXECUTION_TASK_CATALOG_2026-08-28.json`](EXECUTION_TASK_CATALOG_2026-08-28.json).

## 1. Source identity

| Plane | Exact identity |
|---|---|
| approved planning PR | `#5277` |
| approved planning head | `3f041ad26b496e6c03b94de53c7331a5b7532064` |
| planning merge on `dev` | `aeaa0af5707fc85c8baa32cfa2f4d10f368e3b81` |
| execution-catalog base | `origin/dev@d1697f5de815880de015a3bb8498a5b7ed1f0e43` |
| backend audit baseline | `f4a14b29789d6a0e53d64df066042c53bb6c5534` |
| frontend audit baseline | `6766ebe4c95153c019b206cc6326a5a0d9771138` |

Every worker must start from the latest repository `dev` at claim time and repeat a
task-scoped caller/runtime preflight. The audit SHAs explain why the task exists;
they are not stale checkout instructions.

## 2. Materialization boundary

The catalog creates 29 canonical rows:

- one `ARCH-CLEANUP-PLAN-FREEZE-20260828` planning/review task;
- 28 implementation/integration tasks;
- 20 implementation tasks become dependency-ready immediately after plan freeze;
- the Agora entrypoint lane becomes a twenty-first ready lane if the existing PFG
  launcher owner is already terminal; and
- no task is manually started by the materializer. The supervisor and auto-workers
  claim eligible canonical `todo` rows.

All implementation rows depend on the plan-freeze task. This prevents code changes
from starting against an unmerged or partially reviewed task catalog.

The generic assistant packet bridge is not used. It cannot retain the full program,
repository, catalog digest, and artifact-conflict contract needed by this DAG. Tasks
are materialized through the canonical local Human/Ops command and verified by V2
TaskStore readback.

## 3. Complete task set

| Wave | Task | Repository | Owner / reviewer | Main responsibility |
|---:|---|---|---|---|
| 0 | `ARCH-CLEANUP-PLAN-FREEZE-20260828` | Pantheon | Antigravity / Claude | review and merge this exact catalog only |
| 1 | `ACG-BE-GUARDS-20260828` | Pantheon | Antigravity / Claude | normalized route and composition guards |
| 1 | `ACG-BFF-EVOEXP-PREP-20260828` | Pantheon | Claude / Antigravity | Evolution and Experiment durable routers |
| 1 | `ACG-BFF-EVENT-ACTION-PREP-20260828` | Pantheon | Antigravity2 / Claude2 | Events SSE actions and ranking routers |
| 1 | `ACG-RS-FOUNDATION-20260828` | Pantheon | Claude2 / Antigravity2 | 457-method inventory and test-only fixtures |
| 1 | `ACG-RS-OPS-CONSULT-20260828` | Pantheon | Antigravity / Claude | Operations OpenClaw and Consultation ports |
| 1 | `ACG-RS-PERSONA-CAPITAL-20260828` | Pantheon | Claude / Antigravity | Persona Capital Deployment Runtime ports |
| 1 | `ACG-RS-OODA-MGMT-20260828` | Pantheon | Antigravity2 / Claude2 | OODA queues and Management read models |
| 1 | `ACG-RS-RESEARCH-SOURCE-20260828` | Pantheon | Claude2 / Antigravity2 | Research Knowledge Memory Search Source ports |
| 1 | `ACG-RS-LIFECYCLE-GOV-20260828` | Pantheon | Antigravity / Claude | lifecycle telemetry incidents governance lineage |
| 1 | `ACG-RS-PERSONA-TRAIN-20260828` | Pantheon | Claude / Antigravity | Persona Training replay and rapid evaluation |
| 1 | `ACG-LOOP-CONTRACTS-20260828` | Pantheon | Antigravity2 / Claude2 | twelve stable controller contracts and writers |
| 1 | `ACG-LOOP-PROJECTION-20260828` | Pantheon | Claude2 / Antigravity2 | pure exactly-twelve Management projection |
| 1 | `ACG-RUNTIME-MANAGER-20260828` | Pantheon | Antigravity / Claude | deployed Runtime Manager consolidation |
| 1 | `ACG-WORKSHOP-BE-20260828` | Pantheon | Claude / Antigravity | Workshop router/store/public module cleanup |
| 1 | `ACG-SOURCE-INGESTION-20260828` | Pantheon | Antigravity2 / Claude2 | Source Ingestion composition and five routers |
| 1 | `ACG-ENTRYPOINT-WORKER-20260828` | Pantheon | Claude2 / Antigravity2 | truthful package-safe Agora worker launcher |
| 3 | `ACG-DEPLOY-EXACT-GATES-20260828` | Pantheon | Antigravity / Claude | Compose and exact required-component delivery |
| 1 | `ACG-FE-DEPGRAPH-20260828` | execute-plans | Claude / Antigravity | runtime/type SCC and graph gate |
| 2 | `ACG-FE-TRANSPORT-WRITES-20260828` | execute-plans | Antigravity2 / Claude2 | canonical bff-v1 transport and writes |
| 1 | `ACG-FE-REALTIME-V5-20260828` | execute-plans | Claude2 / Antigravity2 | canonical SSE and pure V5 direction |
| 2 | `ACG-FE-LEGACY-DRAIN-20260828` | execute-plans | Antigravity / Claude | 53 production legacy caller migrations |
| 1 | `ACG-FE-LOOP-TRUTH-20260828` | execute-plans | Claude / Antigravity | real nested Management loop truth contract |
| 1 | `ACG-FE-WORKSHOP-20260828` | execute-plans | Antigravity2 / Claude2 | URL-owned Workshop and split receipts |
| 1 | `ACG-FE-DEAD-NL-20260828` | execute-plans | Claude2 / Antigravity2 | delete dead NL and fixed responders |
| 2 | `ACG-BFF-MAIN-CUTOVER-20260828` | Pantheon | Antigravity / Claude | sole `main.py` switch and deletion owner |
| 3 | `ACG-RS-FINAL-DELETE-20260828` | Pantheon | Claude / Antigravity | sole `read_store.py` deletion owner |
| 3 | `ACG-FE-INTEGRATION-20260828` | execute-plans | Antigravity2 / Claude2 | sole bff-v1 barrel and FE acceptance owner |
| 4 | `ACG-INTEGRATION-E2E-20260828` | Pantheon | Claude2 / Antigravity2 | cross-system functional and hosted closure |

No implementation task is owned by Codex. Claude and Antigravity identities are
used as the primary implementation/review pairs; their configured second lanes are
used to increase parallelism while keeping owner and reviewer distinct.

## 4. DAG

```text
ARCH-CLEANUP-PLAN-FREEZE
  |
  +-- 20 independent preparation lanes -------------------------------+
  |                                                                  |
  |   backend guards + router preparation                             |
  |   eight read-model/domain-owner preparations                      |
  |   loop contracts + loop projection                                |
  |   Runtime Manager + Workshop backend + Source Ingestion           |
  |   FE dependency graph + realtime/V5 + loop truth + Workshop + NL  |
  |                                                                  |
  +-- FE-DEPGRAPH --> FE-TRANSPORT-WRITES --+                         |
  |                                         +--> FE-LEGACY-DRAIN -----+-->
  |             FE-REALTIME-V5 -------------+                         |   FE-INTEGRATION
  |             FE-LOOP-TRUTH --------------- serialized overlap -----+
  |
  +-- backend preparations ---------------------------------------------->
  |                                                  BFF-MAIN-CUTOVER
  |                                                         |
  |                                                         +--> RS-FINAL-DELETE
  |
  +-- existing PFG terminal --> ENTRYPOINT-WORKER
  |                                  |
  +-- existing SRCM terminal --------+--> DEPLOY-EXACT-GATES
  |
  +-- BFF-MAIN + RS-FINAL + Runtime + Source + Deploy + FE-INTEGRATION
                                                           |
                                                           v
                                                   INTEGRATION-E2E
```

The long-looking dependency lists do not serialize domain preparation. They protect
only the shared cutover files after all prepared owners are available.

## 5. Exclusive hot-file ownership

| Artifact | Sole switch/delete owner |
|---|---|
| `services/control-plane/bff/main.py` | `ACG-BFF-MAIN-CUTOVER-20260828` |
| `services/control-plane/bff/read_store.py` | `ACG-RS-FINAL-DELETE-20260828` |
| `execute-plans:src/lib/bff-v1/index.ts` | `ACG-FE-INTEGRATION-20260828` |
| Workshop backend router/store entry files | `ACG-WORKSHOP-BE-20260828` |
| `services/source_ingestion/main.py` | `ACG-SOURCE-INGESTION-20260828` |
| Runtime Manager package paths | `ACG-RUNTIME-MANAGER-20260828` |
| `docker-compose.yml` and common deployment verifier | `ACG-DEPLOY-EXACT-GATES-20260828` |

Preparation tasks may create focused routers, typed ports, tests, and evidence. They
may not patch a central hot file to make their own tests pass.

## 6. Existing-task reconciliation

The V2 canonical state had two nonterminal tasks when this catalog was frozen.

### `PFG-HOSTED-ACCEPT-20260820`

Its PR #5287 modifies the Agora worker launcher even though the original task's
artifact list does not name that path. `ACG-ENTRYPOINT-WORKER-20260828` therefore
depends on PFG terminal completion and must first re-audit its landed exact head. It
must retain a complete fix rather than rewrite it.

### `SRCM-P1-HOSTED-ACCEPTANCE-20260824`

It canonically owns `docker-compose.yml`. `ACG-DEPLOY-EXACT-GATES-20260828` depends
on SRCM terminal completion and is the sole subsequent Compose owner. The overlap is
explicitly allowlisted only because the dependency serializes the write.

Completed PFG and prior 12-loop tasks are treated as the implementation baseline.
The new tasks address gaps proven to remain in current code; they do not restore old
task branches or recreate completed mechanisms.

## 7. Disposition coverage

The catalog maps all 102 matrix rows exactly once:

| Priority | Matrix IDs | Count | Execution owners |
|---:|---|---:|---|
| 1 | `ACG-01-*` | 13 | guards/router preparation/Workshop/main cutover |
| 2 | `ACG-02-*` | 21 | domain ports/Workshop/final read-store deletion |
| 3 | `ACG-03-*` | 16 | FE graph/transport/realtime/legacy drain |
| 4 | `ACG-04-*` | 10 | loop contracts/projection/frontend truth |
| 5 | `ACG-05-*` | 6 | Runtime Manager consolidation |
| 6 | `ACG-06-*` | 12 | Workshop backend/frontend |
| 7 | `ACG-07-*` | 6 | Source Ingestion |
| 8 | `ACG-08-*` | 9 | dead NL removal |
| 9 | `ACG-09-*` | 9 | worker entrypoint/exact deployment |

`ACG-FE-INTEGRATION-20260828` and `ACG-INTEGRATION-E2E-20260828` are acceptance
tasks and therefore do not duplicate a disposition row.

## 8. Failure and deletion rule

- Moving callers and deleting the replaced owner are one delivery unit.
- No new global facade, compatibility router, truth store, fixture fallback, or
  synthetic success path may be created.
- A failed E2E run must first publish a task-scoped GAP report naming the canonical
  implementation, caller migration, test assumption, or deployment identity that
  is wrong.
- The integration worker may not immediately add a repair route, alternate store,
  fallback, or new execution task to make a red test green.
