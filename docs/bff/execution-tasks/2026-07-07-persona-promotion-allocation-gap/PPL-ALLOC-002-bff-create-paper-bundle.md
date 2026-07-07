# PPL-ALLOC-002 - BFF Create Paper Persona Bundle

Owner: Claude2
Reviewer: Codex
Depends on: `PPL-ALLOC-001`
Type: BFF implementation task

## Problem

Creating a persona must not leave a passive draft or incomplete shell. A
trading persona should enter the fleet as `paper_running` with its simulation
runtime and isolated paper ledger already bound.

## Scope

- Add or finalize an idempotent BFF command such as
  `POST /bff/management/personas/create-paper-bundle`.
- The command must create or attach:
  - persona identity;
  - mandate;
  - strategy direction;
  - data-source bindings;
  - risk preference / risk policy ref;
  - isolated `paper_ledger_id`;
  - paper runtime binding;
  - paper deployment plan;
  - first evaluation schedule.
- Return a command receipt with created object ids and any failed step.
- If a step fails, persist a repairable incomplete bundle; do not report
  `paper_running`.
- Keep real `capital_pool_id` empty unless an explicit canary/live target is
  under review.

## Acceptance

- Contract tests cover success, idempotent replay, partial failure, and repair
  state.
- A successful response includes `persona_id`, `stage=paper_running`,
  `paper_ledger_id`, `runtime_binding_id`, and `next_action`.
- Tests prove paper creation does not create a live capital binding or broker
  order.
- Persona Fleet read model can display the created bundle without synthetic
  fallback data.

## Validation

```sh
git status -sb
python3 -m pytest services/control-plane/bff/tests/test_bff_persona_create_paper_bundle.py -q
python3 -m pytest services/control-plane/bff/tests/test_bff_pm12_persona_league.py -q
git diff --check
```
