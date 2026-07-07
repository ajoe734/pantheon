# PPL-ALLOC-003 - Capital Binding Read Model

Owner: Gemini2
Reviewer: Claude
Depends on: `PPL-ALLOC-001`
Type: BFF/read-model implementation task

## Problem

Operators cannot safely manage promotion or allocation if the UI makes multiple
personas look like they share one ambiguous paper pool. Paper ledgers, canary
sleeves, live sleeves, and portfolio parent pools must be distinct in the read
models.

## Scope

- Normalize persona fleet rows with:
  - `stage`;
  - `paper_ledger_id`;
  - `runtime_binding_id`;
  - `capital_scope`;
  - `capital_pool_id`;
  - `capital_sleeve_id`;
  - `current_weight`;
  - `target_weight`;
  - `binding_state`.
- Normalize capital pool/sleeve rows with persona binding summaries.
- Preserve legacy paper pool ids only as migration trace, never as the primary
  paper capital identity.
- Add BFF contract tests for paper, canary, live, missing binding, and legacy
  migration rows.

## Acceptance

- Paper personas show isolated `paper_ledger_id` and no real `capital_pool_id`.
- Canary/live personas show a real sleeve or pool id plus current allocation
  weight.
- If multiple personas share a parent portfolio pool, each row still shows a
  distinct sleeve or explicit allocation weight.
- Capital pages can deep-link from persona fleet by paper ledger, sleeve, or
  pool id.

## Validation

```sh
git status -sb
python3 -m pytest services/control-plane/bff/test_pathreon_market_persona_fleet_contract.py -q
python3 -m pytest services/control-plane/bff/tests/test_bff_capital_pool_bindings.py -q
git diff --check
```
