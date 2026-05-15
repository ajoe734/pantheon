# BFF-LUV-SEM-003 — Entity Detail Read Models

Date: 2026-05-09
Owner lane: BFF read model integration
Reviewer lane: control-plane contract review

## Problem

The final contract route registry is complete, but some exact `{id}` aliases are still generic fallback detail payloads. Frontend pages need real DTO projection or an honest 404/degraded envelope per entity family.

Families to audit and complete:

- strategies and personas
- capital pools and rebalances
- deployments and evolution programs
- jobs and runtimes
- MCP servers and MCP tools
- skills, channels, and tools
- ranking formulas and research experiments
- alerts, incidents, audit, and artifacts

## Scope

- Replace generic `{id}` alias fallback with existing `read_store` or service-client projections where available.
- Keep unknown entity behavior truthful: normal source plus missing record is 404, missing source is degraded DTO only when the source is unavailable.
- Add a matrix test covering list-to-detail for seeded records and unknown ids.
- Ensure OpenAPI templates still use the final `{id}` spelling.

## Non-Scope

- Do not fabricate persistent records only to satisfy UI.
- Do not remove existing legacy route spellings unless a compatibility test proves no callers remain.

## Acceptance

- Every final contract detail path has either a real DTO projection or an explicit tested degraded/404 behavior.
- No final detail path returns an untyped generic fallback when seeded data exists.
- Authenticated stub smoke has zero 500 and no unexpected 503.
- `python3 -m pytest services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -q` passes with added detail matrix coverage.

## Delivery Refresh — 2026-05-09

The review pass found that several final aliases were still only route-registration placeholders:

- `/bff/ranking-formulas` and `/bff/ranking-formulas/{id}`
- `/bff/research-experiments` and `/bff/research-experiments/{id}`
- `/bff/artifacts` and `/bff/artifacts/{id}`
- `/bff/mcp-servers`, `/bff/mcp-tools`, and `/bff/channels` detail/list aliases

The implementation now routes these aliases through concrete read-model projections:

- read-store backed DTOs for ranking formulas, research experiments, and artifacts
- local registry DTOs for MCP servers, MCP tools, channels, and v5 interventions
- alert detail projection from the composed operator alert feed
- 404 for unknown ids when a source or registry exists
- degraded detail DTO only when the read-model source is unavailable

The final wiring contract test now includes a seeded list-to-detail matrix across strategies, personas, capital pools, rebalances, deployments, runtimes, research experiments, artifacts, ranking formulas, MCP servers/tools, skills, channels, tools, incidents, alerts, and v5 interventions.

Verification:

- `python3 -m pytest services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -q` -> 7 passed, 3 warnings.
- `python3 -m pytest services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/test_bff_session_auth_me_contract.py services/control-plane/bff/test_final_command_execution_bridge.py -q` -> 25 passed, 4 warnings.
- `python3 -m pytest services/control-plane/bff/test_bff_capital_ranking_rebalance_contract.py services/control-plane/bff/test_bff_evolution_experiment_jobs_events_contract.py -q` -> 53 passed, 10 warnings.

Known adjacent note:

- `python3 -m pytest services/control-plane/bff/test_read_store_loop_sentinel.py -q` was not used as this task's acceptance check; it currently fails during collection because it imports `services.control_plane.bff`, while this repo path is `services/control-plane/bff`.
