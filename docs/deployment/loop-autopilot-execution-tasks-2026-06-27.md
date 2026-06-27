# Loop Autopilot Execution Tasks - 2026-06-27

## Source Plan

Primary plan:

- `docs/04/pantheon_sa/SA-21_global_loop_inventory_autopilot_execution_plan.md`

This file records the active execution-task materialization of SA-21. The
canonical task rows are in `ai-status.json`; this document is the stable task
DAG reference for supervisor and auto-worker dispatch.

## Dispatch Rules

All `LOOP-AUTO-*` tasks inherit these rules:

- no task may close with only seeds, fixtures, static metadata, or panel copy;
- no `api-only` task may claim `reconciled` without a durable worker or
  controller;
- no scheduled task may claim `reconciled` unless it repairs drift, not only
  calls an endpoint;
- BFF may aggregate truth but must not become an authoritative domain writer
  unless an L1 policy already grants that authority;
- duplicate ticks, duplicate events, and worker restarts must be harmless;
- production-affecting mutations must pass the relevant governance gate;
- each task must leave operator-visible truth or evidence that prevents a green
  panel from hiding an unowned loop.

## Task DAG

| Task | Owner | Reviewer | Depends on | Purpose |
| --- | --- | --- | --- | --- |
| `LOOP-AUTO-000` | Codex | Claude | - | Define the loop catalog schema and maturity registry. |
| `LOOP-AUTO-001` | Codex2 | Claude | `LOOP-AUTO-000` | Publish a current loop inventory read model. |
| `LOOP-AUTO-002` | Claude | Codex | `LOOP-AUTO-000` | Add completion guardrails for loop maturity claims. |
| `LOOP-AUTO-SRC-001` | Copilot | Codex | `LOOP-AUTO-000` | Add first-class persona data requirement schema. |
| `LOOP-AUTO-SRC-002` | Copilot | Claude | `LOOP-AUTO-SRC-001` | Reconcile persona requirements into source connectors and schedules. |
| `LOOP-AUTO-SRC-003` | Gemini | Codex | `LOOP-AUTO-SRC-002` | Harden source scheduler supervision and missed-tick visibility. |
| `LOOP-AUTO-SRC-004` | Codex2 | Claude | `LOOP-AUTO-SRC-002`, `LOOP-AUTO-SRC-003` | Wire SourceHealth truth into persona and BFF panels. |
| `LOOP-AUTO-SRC-005` | Copilot | Codex | `LOOP-AUTO-SRC-003` | Connect completed source runs to search index refresh truth. |
| `LOOP-AUTO-RT-001` | Claude | Codex | `LOOP-AUTO-000` | Define runtime fleet desired-state query. |
| `LOOP-AUTO-RT-002` | Gemini | Claude | `LOOP-AUTO-RT-001` | Implement managed paper runtime fleet reconciliation. |
| `LOOP-AUTO-RT-003` | Codex | Claude2 | `LOOP-AUTO-RT-002` | Reap stale runtime monitoring sessions and align restarts. |
| `LOOP-AUTO-RT-004` | Gemini2 | Claude | `LOOP-AUTO-RT-002` | Isolate runtime signal consumption by binding or runtime identity. |
| `LOOP-AUTO-RT-005` | Codex2 | Claude | `LOOP-AUTO-RT-002`, `LOOP-AUTO-RT-003`, `LOOP-AUTO-RT-004` | Produce runtime fleet recovery evidence. |
| `LOOP-AUTO-DEP-001` | Claude | Codex | `LOOP-AUTO-000` | Add a deployment saga outbox consumer. |
| `LOOP-AUTO-DEP-002` | Claude2 | Codex | `LOOP-AUTO-DEP-001`, `LOOP-AUTO-RT-001` | Add idempotent runtime-manager dispatch adapter. |
| `LOOP-AUTO-DEP-003` | Codex | Claude | `LOOP-AUTO-DEP-001`, `LOOP-AUTO-DEP-002` | Add saga progress feedback, retry, and DLQ behavior. |
| `LOOP-AUTO-DEP-004` | Codex2 | Claude | `LOOP-AUTO-DEP-003` | Split promotion/deployment BFF truth by stage. |
| `LOOP-AUTO-TEL-001` | Codex | Claude | `LOOP-AUTO-000` | Audit telemetry readiness, writer durability, DLQ, and replay. |
| `LOOP-AUTO-TEL-002` | Claude | Codex | `LOOP-AUTO-TEL-001`, `LOOP-AUTO-RT-002` | Add scheduled reconciliation from telemetry truth. |
| `LOOP-AUTO-TEL-003` | Claude2 | Codex | `LOOP-AUTO-TEL-002` | Add incident-triggered reconciliation listener. |
| `LOOP-AUTO-TEL-004` | Codex2 | Claude | `LOOP-AUTO-TEL-002`, `LOOP-AUTO-TEL-003` | Open or update incidents from drift reports with dedupe. |
| `LOOP-AUTO-TEL-005` | Gemini2 | Codex | `LOOP-AUTO-TEL-004` | Add replay and operator evidence for telemetry-driven incidents. |
| `LOOP-AUTO-EVO-001` | Claude2 | Codex | `LOOP-AUTO-TEL-004` | Create postmortem drafts from resolved incidents. |
| `LOOP-AUTO-EVO-002` | Codex | Claude | `LOOP-AUTO-EVO-001` | Bridge published postmortems into evolution proposals. |
| `LOOP-AUTO-EVO-003` | Gemini | Codex | `LOOP-AUTO-EVO-002` | Add evolution daily sweep with threshold and cooldown policy. |
| `LOOP-AUTO-EVO-004` | Claude | Codex | `LOOP-AUTO-EVO-002`, `LOOP-AUTO-DEP-001` | Dispatch approved evolution actions through gated paths. |
| `LOOP-AUTO-EVO-005` | Gemini2 | Claude | `LOOP-AUTO-EVO-004` | Prove rollback and follow-through for approved evolution actions. |
| `LOOP-AUTO-KNOW-001` | Copilot | Codex | `LOOP-AUTO-SRC-005` | Add source-to-strategy distillation worker. |
| `LOOP-AUTO-KNOW-002` | Copilot | Claude | `LOOP-AUTO-KNOW-001` | Add alpha replication queue and scheduled revalidation. |
| `LOOP-AUTO-KNOW-003` | Codex2 | Claude | `LOOP-AUTO-SRC-004` | Add persona teaching async preview and evaluation worker. |
| `LOOP-AUTO-KNOW-004` | Copilot | Codex | `LOOP-AUTO-KNOW-003` | Extract Agora interaction evidence into governed datasets. |
| `LOOP-AUTO-KNOW-005` | Gemini | Codex | `LOOP-AUTO-KNOW-004`, `LOOP-AUTO-TEL-005` | Add human imitation and shadow evaluation scheduler. |
| `LOOP-AUTO-KNOW-006` | Claude | Codex | `LOOP-AUTO-KNOW-001` | Add durable consultation committee and red-team workflow executor. |
| `LOOP-AUTO-BFF-001` | Codex | Claude | `LOOP-AUTO-001` | Add loop health read model for operator truth. |
| `LOOP-AUTO-BFF-002` | Gemini2 | Codex | `LOOP-AUTO-BFF-001`, `LOOP-AUTO-TEL-001` | Add BFF downstream health monitor that emits telemetry and incidents. |
| `LOOP-AUTO-BFF-003` | Codex2 | Claude | `LOOP-AUTO-BFF-001` | Label seed, fixture, snapshot, registry, scheduled, and live truth in panels. |
| `LOOP-AUTO-BFF-004` | Claude2 | Codex | `LOOP-AUTO-SRC-004`, `LOOP-AUTO-RT-005`, `LOOP-AUTO-DEP-004`, `LOOP-AUTO-TEL-005`, `LOOP-AUTO-EVO-005`, `LOOP-AUTO-KNOW-006`, `LOOP-AUTO-BFF-003` | Run cross-loop operator drills and close the autopilot wave. |

## Completion Boundary

This materialization only dispatches tasks. It does not claim any loop has moved
from `manual` or `api-only` to `reconciled` or `proven-live`. Each worker must
raise maturity only with merged code, local validation, and the relevant
operator evidence packet.
