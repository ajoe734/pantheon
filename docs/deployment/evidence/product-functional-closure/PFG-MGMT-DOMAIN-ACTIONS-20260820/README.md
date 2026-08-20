# PFG-MGMT-DOMAIN-ACTIONS-20260820 Evidence

## Scope & Objective
Map every production Management action to its authoritative domain owner or fail closed with typed unavailable errors (`ActionUnavailableError`), eliminating generic fake admission stubs.

## Summary of Accomplishments
1. **Modular Domain Command Adapters (`services/control-plane/bff/command_adapters/`)**:
   - `capital_adapter.py`: Direct routing to Capital authority (`/api/capital-pools`, `/api/rebalances`, `/api/bindings`, `/api/containment/emergency`).
   - `runtime_adapter.py`: Direct routing to Runtime manager / repair APIs (`/api/runtimes/*`, `/api/rollbacks/*`, `/api/telemetry/*`).
   - `deployment_adapter.py`: Direct routing to Deployment service (`/api/deployments`).
   - `persona_adapter.py`: Direct routing to Persona lifecycle & emergency containment.
   - `governance_adapter.py`: Direct routing to Governance decision & human-gate APIs.
   - `incident_adapter.py`: Direct routing to Incident, Sentinel, and Risk alert APIs.
   - `evolution_adapter.py`: Direct routing to Evolution proposal, program, and job APIs.
   - `strategy_adapter.py`: Direct routing to Strategy & Ranking service APIs.
   - `capabilities_adapter.py`: Enforces fail-closed posture for unbacked runtime capability mutations (`CAPABILITY_ACTION_UNAVAILABLE`, HTTP 422).
   - `agora_adapter.py` & `audit_adapter.py`: Direct routing for Agora feedback and audit exports.
   - `registry.py`: Central registry and `dispatch_domain_command` dispatcher.

2. **Refactored BFF Command Executor (`services/control-plane/bff/command_executor.py`)**:
   - Replaced generic admission-only stubs in `_EXECUTORS` with dedicated domain adapter calls via `dispatch_domain_command`.
   - Handled `ActionUnavailableError` returning structured `CommandStatus.FAILED` with typed 422 error details and remediation instructions.

3. **Validation**:
   - 61 unit and contract tests in `test_command_executor.py` and `test_management_domain_actions.py` passing 100%.
