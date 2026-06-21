# Pantheon Agora — Design Closure Round 2 / v1.3

**Date:** 2026-06-21  
**Status:** SD response package; must be merged as an additive v1.3 contract bundle before the blocked execution tasks are resumed.  
**Baseline inspected:** `pantheon@dev` through `agora_v1_2.openapi.yaml` and `bundle_index.v1_2.json`; `execute-plans@dev` IA decision and current frontend dependency state.  
**Conflict rule:** v1, v1.1 and v1.2 bundles remain immutable. This pack adds v1.3 artifacts under `services/control-plane/specs/agora/v4/` plus `agora_v1_3.openapi.yaml`.

## Decisions

1. Use restricted RFC 6902 JSON Patch for StrategySpec version proposals.
2. Add three deterministic readiness gates: preliminary research, full validation, trading room.
3. Make research plan-first: a run cannot bypass an approved ResearchPlan.
4. Route research by typed stage; the LLM may propose stage requirements but cannot invoke arbitrary framework routes.
5. Replace the generic workshop stream with a typed, ordered, replayable aggregate-event contract.
6. Define a Trading Room read aggregate and decision-support queue. Agora never routes orders.
7. Define field-level WorkshopCard projections for the frontend.
8. Freeze one canonical winner-branch E2E and one cross-repo/cross-user/isolation acceptance matrix.

## Files

- `01_strategy_versioning_patch_readiness.md`
- `02_research_facade_run_projection.md`
- `03_workshop_sse_contract.md`
- `04_trading_room_and_governed_intent.md`
- `05_workshop_card_contracts.md`
- `06_winner_branch_e2e_and_isolation.md`
- `07_dispatch_unblock_matrix.md`
- `08_openapi_v1_3_delta.yaml`
- `schemas/*.json`

## Canonical repo targets

```text
docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/
services/control-plane/specs/agora/v4/
services/control-plane/specs/agora/v4/capability_manifest_v1_3.json
services/control-plane/openapi/agora_v1_3.openapi.yaml
services/control-plane/specs/agora/bundle_index.v1_3.json
```

## Mandatory bundle rule

`bundle_index.v1_3.json` must:

- extend `services/control-plane/specs/agora/bundle_index.v1_2.json`;
- include the exact-byte SHA-256 of the v1.2 index;
- include every v4 schema, the v1.3 capability manifest and `agora_v1_3.openapi.yaml`;
- be generated after merge, not copied blindly from this design package.
