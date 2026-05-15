# BFF-LUV-SEM-005 — Agora Extended Semantics

Date: 2026-05-09
Owner lane: Agora / BFF read-write integration
Reviewer lane: control-plane contract review

## Problem

The Agora long-tail routes are contract-visible, but several are still empty-list fallback or generic command receipt surfaces.

Affected areas:

- ask sessions and ask submission
- inbox
- postmortems
- skill-coaching sessions
- persona-lab runs
- evaluation suites and evaluation runs
- signal detail and signal feedback

## Scope

- Wire routes to existing Agora/read-store datasets where available.
- Add missing read-store dataset adapters for skill coaching, persona lab, and evaluations if no adapter exists.
- Keep degraded metadata honest when a dataset is absent.
- Add seeded tests for every long-tail Agora surface listed above.

## Non-Scope

- Do not make LLM calls from route handlers.
- Do not store UI-only mock objects as canonical Agora state.

## Acceptance

- Each affected Agora route returns seeded data when the read-store contains it.
- Empty fallback is allowed only with explicit degraded or empty-source metadata.
- Signal feedback validates payload shape and writes a real feedback record or command.
- `test_bff_agora_core_contract.py`, extended Agora tests, and final live wiring tests pass.

## Delivery Notes

- Added explicit read-store dataset adapters for:
  - `agora_skill_coaching_sessions`
  - `agora_persona_lab_runs`
  - `agora_evaluation_suites`
  - `agora_evaluation_runs`
- Rewired final Agora extended routes to read from store-backed datasets with route-specific surface metadata:
  - `GET /bff/agora/inbox`
  - `GET /bff/agora/ask/sessions`
  - `GET /bff/agora/skill-coaching/sessions`
  - `GET /bff/agora/persona-lab/runs`
  - `GET /bff/agora/postmortems`
  - `GET /bff/agora/evaluation-suites`
  - `GET /bff/agora/evaluation-runs`
- Replaced `POST /bff/agora/ask` generic receipt behavior with a persisted Agora session/message write plus an `AgoraMessageAction` command receipt. No LLM call is made in the route handler.
- Kept empty fallback explicit: absent skill-coaching data now returns an empty list with `source=missing`, `status=unavailable`, and degradation metadata instead of an unqualified empty success.
- Existing signal feedback semantics remain record-backed and command-backed; tests continue to cover payload validation, feedback persistence, signal update, and idempotent replay.
- Codex2 review hardening replaced the SEM list helper's private `_data` read with `ReadSurfaceStore` dataset reader/source semantics, added service-backed adapters for Agora sessions/signals plus skill-coaching/persona-lab/evaluation datasets, and moved `POST /bff/agora/ask` onto the existing Agora session/message store methods.

## Verification

- `python3 -m pytest services/control-plane/bff/test_bff_agora_extended_contract.py -q` -> `8 passed`
- `python3 -m pytest services/control-plane/bff/test_bff_agora_core_contract.py services/control-plane/bff/test_bff_agora_extended_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py -q` -> `20 passed`
- `python3 -m pytest services/control-plane/bff/test_bff_agora_core_contract.py services/control-plane/bff/test_bff_agora_extended_contract.py services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py services/control-plane/bff/test_final_command_execution_bridge.py -q` -> `25 passed`
- `python3 -m pytest services/control-plane/bff/test_bff_capital_ranking_rebalance_contract.py services/control-plane/bff/test_bff_evolution_experiment_jobs_events_contract.py -q` -> `53 passed`

Observed warnings are pre-existing focused-suite warnings:

- `read_store.py` uses deprecated `datetime.utcnow()` in existing helper code.
- FastAPI emits a duplicate OpenAPI operation id warning for an existing OpenClaw readiness route.
