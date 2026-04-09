# OSS-001 Review (Codex)

Status: changes requested
Task: `OSS-001`
Reviewer: Codex
Date: 2026-04-10

## Findings

### 1. Blocking: smoke test Step 4/5 cannot pass against the canonical `StrategySpec` schema

`integrations/openclaw/smoke_test.md` claims Step 4 writes a normalized `StrategySpec` and Step 5 validates it against `services/control-plane/specs/strategy_spec.schema.json`.

That payload is not a valid `StrategySpec`:

- it emits `strategy_name` and `description` instead of canonical `title` and `hypothesis`
- it omits required fields such as `spec_version`, `strategy_id`, `objective`, `market_scope`, `data_dependencies`, `execution_profile`, `evaluation_plan`, `governance`, and `provenance`
- it adds `governance_context`, `registry_hints`, and `source_payload`, which are not allowed by the `StrategySpec` schema

Evidence:

- `integrations/openclaw/smoke_test.md:97-164`
- `integrations/openclaw/smoke_test.md:237-273`
- `services/control-plane/specs/strategy_spec.schema.json:6-18`
- `services/control-plane/specs/strategy_spec.schema.json:19-206`

Result: if executed as written, Step 5 schema validation will fail. This blocks approval because the documented smoke-test plan is not actually executable against the repo's canonical schema.

### 2. Blocking: governance doc assigns `WorkflowHandoff`-only fields to `StrategySpec`

`integrations/openclaw/governance.md` §5.2 says every normalized `StrategySpec` must carry `governance_context` and `registry_hints`.

That is not the current canonical object boundary in this repo:

- `StrategySpec` carries `governance` and `provenance`
- `WorkflowHandoff` carries `registry_hints`, `governance_context`, and outer `provenance`

The document also uses `registry_hints.lifecycle_state`, but the canonical `WorkflowHandoff` field is `registry_hints.initial_lifecycle_state`.

Evidence:

- `integrations/openclaw/governance.md:94-115`
- `services/control-plane/specs/strategy_spec.schema.json:154-204`
- `services/control-plane/specs/workflow_handoff.schema.json:70-162`
- `services/research/strategy_spec/normalizer.py:152-185`

Result: the documented adapter boundary drifts from OC-003 and directly causes the smoke-test payload shape to mix `StrategySpec` and `WorkflowHandoff`.

## Required Fix Direction

Use the repo's existing OC-003 split:

1. Build a canonical `StrategySpec` that validates against `services/control-plane/specs/strategy_spec.schema.json`.
2. Wrap it in a canonical `WorkflowHandoff` that carries `registry_hints`, `governance_context`, and handoff provenance.
3. Update `integrations/openclaw/governance.md` so it assigns those fields to `WorkflowHandoff`, not `StrategySpec`.
4. Update `integrations/openclaw/smoke_test.md` so Step 4 emits either:
   - both `strategy_spec.json` and `workflow_handoff.json`, or
   - just `workflow_handoff.json` containing an inline canonical `strategy_spec`.
5. Validate with the correct schema target. The existing reference implementation is already in:
   - `services/research/strategy_spec/normalizer.py`
   - `services/control-plane/cron/schema_validation.py`

## Non-blocking Follow-up

- After the schema split is fixed, align the workspace path examples in `smoke_test.md` (`/tmp/openclaw-smoke-test` vs `/tmp/openclaw-smoke`) so the doc reads as one executable path.
