# AUD-002 Acceptance Evidence

Task: AuditAction backend (write engine)
Owner: Codex
Reviewer: Claude

## Delivered

- Added command-store audit projection for `governance_audit_events` readers.
- Added foundation `AuditAction` context to semantic command, governance/risk/incident, capital, strategy/persona, evolution/experiment, and tools/MCP/skills action write helpers.
- Kept existing fixture-based audit reads intact while allowing command-backed audit events to appear in `/bff/audit`, `/bff/audit/events`, `/bff/audit/entities/{type}/{id}`, review/strategy/persona audit subresources, and the semantic `/bff/audit` list adapter.
- Added regression coverage for runtime action audit writes and `POST /bff/audit/export` audit writes without local snapshot fallback.

## Verification

- `PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/test_aud_002_audit_action_write_engine.py` passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_aud_002_audit_action_write_engine.py services/control-plane/bff/test_bff_audit_contract.py services/control-plane/bff/test_final_command_execution_bridge.py services/control-plane/bff/tests/test_command_replay_conflict.py services/control-plane/bff/tests/test_actions_to_commands_adapter.py -q` passed: 35 passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_bff_capital_ranking_rebalance_contract.py services/control-plane/bff/test_bff_strategy_persona_contract.py services/control-plane/bff/test_bff_evolution_experiment_jobs_events_contract.py services/control-plane/bff/test_bff_agora_extended_contract.py -q` passed: 80 passed, 10 warnings from an existing `datetime.utcnow()` deprecation in `read_store.py`.
- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_governance_command_submission.py -q` passed: 21 passed.

## Finalization

- Review approved by Claude in `services/control-plane/bff/review_aud_002_claude.md`.
- Closeout verification repeated on 2026-05-16 with the same commands above: py_compile passed; 35 passed; 80 passed with 10 existing `datetime.utcnow()` warnings; 21 passed.

## Known Non-AUD-002 Failure

- `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_bff_governance_runtime_risk_audit_contract.py -q` still has one pre-existing fixture/order failure:
  `test_bff_incident_routes_support_create_detail_and_action` expects first incident `inc-20260410-001`, but current fixture ordering returns `inc-pack-c-001`.
