# BFF-LUV-GAP-002 - Strategy And Persona BFF Compatibility

Priority: P0

Area: Management registry routes

## Goal

Expose the strategy and persona route families expected by `execute-plans` while preserving the existing `/api/v1/*` Pantheon BFF surfaces.

## Missing Routes

Strategy:

- `GET /bff/strategies`
- `POST /bff/strategies`
- `GET /bff/strategies/{strategyId}`
- `PATCH /bff/strategies/{strategyId}`
- `GET /bff/strategies/{strategyId}/specs`
- `POST /bff/strategies/{strategyId}/specs`
- `GET /bff/strategies/{strategyId}/experiments`
- `GET /bff/strategies/{strategyId}/artifacts`
- `GET /bff/strategies/{strategyId}/lineage`
- `GET /bff/strategies/{strategyId}/audit`
- `POST /bff/strategies/{strategyId}/actions/{actionId}`
- `POST /bff/strategies/{strategyId}/dry-run`

Persona:

- `GET /bff/personas`
- `POST /bff/personas`
- `GET /bff/personas/{personaId}`
- `PATCH /bff/personas/{personaId}`
- `GET /bff/personas/{personaId}/route-policy`
- `GET /bff/personas/{personaId}/activity`
- `GET /bff/personas/{personaId}/evaluations`
- `GET /bff/personas/{personaId}/memory`
- `GET /bff/personas/{personaId}/audit`
- `POST /bff/personas/{personaId}/actions/{actionId}`
- `POST /bff/personas/{personaId}/test-prompt`

Platform helper:

- `GET /bff/search`
- `/bff/types` source-reference compatibility decision

## Implementation Notes

- Reuse existing read-store adapters and `/api/v1/personas`, `/api/v1/personas/{id}`, `/api/v1/personas/{id}/sessions`, strategy/workbench data where possible.
- Return frontend-ready DTOs compatible with `execute-plans/src/lib/bff/client.ts` and v3/v4 descriptors.
- High-risk actions must use the final command response and precondition envelope from `BFF-FINAL-001..003`.

## Acceptance Criteria

- Exact routes above return 200/201/202/204 or a final BFF error envelope, never 404.
- List routes return `ListResponse<T>` compatible envelopes where Part 06/Pack D require list semantics.
- Strategy/persona action routes map to the canonical action catalog.
- Focused tests cover list, detail, action happy path, action precondition failure, and missing entity behavior.

## Delivery Notes

Implemented in:

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/models.py`
- `services/control-plane/bff/action_catalog.py`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/test_bff_strategy_persona_contract.py`

The `/bff/strategies` and `/bff/personas` route families now expose list, create, detail, patch, subresource, action, dry-run/test-prompt, search, and `/bff/types` compatibility surfaces. Created/updated strategy and persona records are held in task-local BFF overlays for frontend round-trip compatibility while canonical read data still comes from `strategy_specs` and `personas`.

Action routes use `CommandType.STRATEGY_ACTION` / `CommandType.PERSONA_ACTION`, `ObjectType.STRATEGY` / `ObjectType.PERSONA`, the shared idempotency/precondition error helpers, and canonical action catalog entries:

- `StrategyAction` -> `/bff/strategies/{strategy_id}/actions/{action_id}`
- `PersonaAction` -> `/bff/personas/{persona_id}/actions/{action_id}`

## Verification

Passed:

```bash
python3 -m pytest services/control-plane/bff/test_bff_strategy_persona_contract.py -q
# 15 passed
python3 -m pytest services/control-plane/bff/test_execute_plans_contract_registry.py -q
# 5 passed
```

Observed cross-task worktree issue:

```bash
python3 -m pytest services/control-plane/bff/test_action_catalog.py -q
```

This fails because the current shared worktree has BFF-LUV-GAP-006 Agora `CommandType` values (`AgoraSignalFeedback`, `AgoraMessageAction`, `AgoraInsightAction`, `AgoraMemoryAction`) without matching catalog entries yet. The BFF-LUV-GAP-002 `StrategyAction` and `PersonaAction` catalog entries are present and are asserted by the focused strategy/persona contract test.

## Review Approval

Reviewer: Codex
Date: 2026-05-08T16:57:51Z

Approved: /bff/strategies, /bff/personas, /bff/search, and /bff/types compatibility surfaces are present; StrategyAction and PersonaAction use final command envelopes with idempotency/precondition errors. Verification by reviewer: 15 passed (strategy/persona contract) + 5 passed (registry). Note: implementation appears in HEAD via commit 777533ee (bundled by parallel worker under BFF-LUV-GAP-008 subject); task-specific test/artifact commit is 0a4b7b5b.

## Finalization

Owner: Claude
Date: 2026-05-08

Final verification confirmed implementation still passes after subsequent worktree changes:
- `python3 -m pytest services/control-plane/bff/test_bff_strategy_persona_contract.py -q` → 15 passed
- `python3 -m pytest services/control-plane/bff/test_execute_plans_contract_registry.py -q` → 5 passed

Task closed in `done` state. All acceptance criteria met: strategy routes non-404, persona routes non-404, action precondition errors use final envelope.
