# Review: SVC-RESEARCH-GATEWAY-DORMANT-CAPABILITY-SURFACE

**Reviewer:** Claude
**Date:** 2026-04-29
**Task:** Expose fail-closed dormant backend capability surface

## Verification

```
python3 -m pytest services/research-worker-gateway/tests/test_research_worker_gateway_http_service.py services/research-worker-gateway/tests/test_research_worker_gateway_rejection_policy.py -q
9 passed in 1.70s

python3 -m pytest services/research/tests/test_research_orchestrator_http_service.py services/policy-learning/tests/test_policy_learning_http_service.py -q
7 passed in 1.74s
```

Total: 16 passed.

## Artifacts Reviewed

- `services/research-worker-gateway/main.py` — WORKER_REGISTRY with gate_state/allowed_scope, _rejection_for policy, capabilities endpoint
- `services/research-worker-gateway/tests/test_research_worker_gateway_http_service.py` — lifecycle, idempotency, status, cancel tests
- `services/research-worker-gateway/tests/test_research_worker_gateway_rejection_policy.py` — production/live rejection, execution/registry/governance denial, capability surface, legacy env, openclaw-specific tests
- `services/research/main.py` — CAPABILITY_REGISTRY with gate_state/allowed_scope, dispatch run rejection policy, capabilities endpoint
- `services/research/tests/test_research_orchestrator_http_service.py` — full lifecycle, production adapter blocking, write-path rejection, legacy env fail-closed tests
- `services/policy-learning/main.py` — CAPABILITY_REGISTRY, proposal rejection policy, capabilities endpoint
- `services/policy-learning/tests/test_policy_learning_http_service.py` — stub lifecycle, dormant execution rejection, write-path and unknown adapter tests
- `RESEARCH_BACKEND_MATURITY_MATRIX.md` — updated Current Status section and Existing Evidence to reference the three service main.py files

## Acceptance Criteria Check

| Criterion | Status | Notes |
|---|---|---|
| capabilities list OpenClaw/Qlib/TRL/FinRL/RLlib/Ray Tune/W&B with gate_state and allowed_scope | ✅ | All 7 backends appear in all three service capability endpoints with `gate_state=fail_closed` and `allowed_scope=capability_metadata_read_only` |
| dispatch rejects production/paper/canary/live and registry/governance writing paths | ✅ | All three services reject PRODUCTION_ADAPTERS, PRODUCTION_MODES, registry_write*, governance_write* tokens; verified by tests |
| unknown versus gated workers return stable policy errors | ✅ | Gated known workers → `production_adapter_disabled`; unregistered workers → `unknown_worker`/`unknown_adapter`; stable error shapes |
| tests cover dormant capability read versus denied execution | ✅ | `test_capabilities_list_activation_gated_backend_inventory` reads capabilities (200 OK) then attempts dispatch (rejected) for each dormant worker; `test_dormant_capabilities_are_readable_but_execution_is_rejected_by_default` does the same for policy-learning |

## Implementation Notes

- **research-worker-gateway**: WORKER_REGISTRY covers the 7 gated backends plus stub/handoff_only/manual safe workers. The rejection order in `_rejection_for` correctly handles: EP5/learning activation → execution plane paths → registry writes → governance writes → production adapter/mode → unknown worker → unsafe dispatch mode → safe worker gating. The legacy production env check (`RESEARCH_WORKER_GATEWAY_ENABLE_PRODUCTION_ADAPTERS=true`) does not bypass the worker-level gate — tested and verified.
- **research-orchestrator**: CAPABILITY_REGISTRY mirrors the same 7 backends with identical gate metadata. Dispatch rejection covers unknown adapter before production adapter, then registry/governance tokens. All artifacts are forced to `artifact_state=draft` and `deployment_stage=none`; governance.direct_live_influence is hardcoded to `False`.
- **policy-learning**: CAPABILITY_REGISTRY is consistent with the other two surfaces. Proposal rejection follows the same precedence: registry write → governance write → unknown adapter → production adapter.
- **RESEARCH_BACKEND_MATURITY_MATRIX.md**: Current Status section explicitly describes the dormant capability surface at all three service boundaries with correct read-only scope semantics. The three service main.py files are listed under Existing Evidence.
- No production adapters can be activated by any env variable in the current implementation — the gate is structural, not config-driven.

## Decision

**APPROVED** — all four acceptance criteria met, 16 tests pass, implementation is consistent across all three service boundaries, and the maturity matrix accurately reflects the delivered scope without overclaiming production activation.
