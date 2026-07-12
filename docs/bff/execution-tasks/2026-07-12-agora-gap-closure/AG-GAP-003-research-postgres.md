# AG-GAP-003: Durable Postgres store for research

## Scope

`services/control-plane/bff/agora/research/store.py` is pure in-memory
(`MemoryResearchPlanStore`; factory comment "only memory for now" at
store.py:334-337). Research plans, runs, artifacts, and candidate pools
(scores, member reviews, discussions, monitoring) are lost on restart.

## Work

1. Implement a Postgres-backed research store following the AG-GAP-001/002
   backend-selection convention (`AGORA_RESEARCH_STORE_BACKEND=postgres`).
2. Cover both aggregate families: research plans/runs/artifacts and
   candidate pools with their collaboration sub-resources.
3. Preserve plan-first governance (runs cannot bypass an approved plan) and
   tenant/user scoping.
4. Enable on dev after merge.

## Acceptance

- All 27 research routes behave identically on both backends; existing tests
  parametrized across backends.
- Live restart-persistence proof for one research plan (draft -> approve ->
  run) and one candidate pool with a score + member review.
- Post-deploy live curl proof recorded under `docs/deployment/evidence/ag-gap-003/`.

## References

- `services/control-plane/bff/agora/research/store.py:16-337`
- `services/control-plane/bff/agora/research/router.py`
- `services/control-plane/specs/agora/v4/` (research_plan_execution, research_run_projection)
- `services/control-plane/specs/agora/v5/` (candidate_* contracts)
