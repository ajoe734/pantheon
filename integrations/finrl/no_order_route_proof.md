# FinRL No-Order-Route Proof

Task: `RES-ACT-RL-001-V2`
Owner: `Codex`
Reviewer: `Claude2`
Status: adapter-specific proof artifact

## Scope

This proof records the current FinRL research-activation boundary against the
generic `ProductionDataProof.v1` shape delivered by `RES-ACT-001-V2`.

It is not a production registry write, deployment-stage change, broker route,
runtime binding, or capital-binding claim. The FinRL outputs stay research-only
or registry-review-only until a separate registry service admits them.

## Evidence Inputs

The proof is based on two already reviewed FinRL evidence lanes:

| Evidence | Path |
|---|---|
| Runtime smoke evidence summary | `support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/finrl_activation_evidence_summary.json` |
| Runtime smoke dataset evidence | `support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/finrl_dataset_evidence.json` |
| Runtime smoke artifact bundle | `support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/finrl_artifact_bundle.json` |
| Runtime smoke candidate packet | `support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/finrl_candidate_packet.json` |
| Runtime smoke registry entry | `support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/finrl_registry_entry.json` |
| Runtime smoke real-backend attempt | `support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/finrl_real_backend_attempt.json` |
| Runtime smoke manifest | `support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/manifest.json` |
| FinRL registry admission packet | `support/evidence/OSS-FINRL-V2-001/admission_packet.json` |
| No-order-route scanner | `services/governance/research_activation/no_order_route_scanner.py` |
| Generic OOS harness | `services/governance/research_activation/oos_runner.py` |

The runtime-smoke source ref is
`synthetic-ohlcv://bounded-fixture/30-periods`. It is bounded research evidence,
not a live broker or market-data order route.

## ProductionDataProof Mapping

| Field | FinRL value |
|---|---|
| `schema_version` | `ProductionDataProof.v1` |
| `activation_tier` | `R3` |
| `adapter_kind` | `finrl` |
| `adapter_id` | `finrl-no-order-route-proof-20260502` |
| `source_dataset_refs` | `synthetic-ohlcv://bounded-fixture/30-periods` |
| `provider.name` | `Pantheon bounded RL OHLCV fixture` |
| `provider.source_class` | `research_grade` |
| `provider.dataset_id` | `finrl-activation-smoke-20260501` |
| `entitlement.allowed_use` | `research`, `model_training`, `evaluation` |
| `point_in_time.event_time_field` | `date` |
| `point_in_time.available_time_field` | `created_at` |
| `storage.backend` | `object_store` |
| `audit.evidence_bundle_ref` | `support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/` |
| `no_order_route.produced_artifact_types` | `model_artifact`, `evaluation_result`, `candidate_packet`, `registry_admission_packet` |
| `no_order_route.execution_targets` | `research`, `registry_review` |

The point-in-time claim is limited to the bounded OHLCV evidence instance. This
proof does not claim a live production dataset entitlement or authorize a
production activation.

## No-Order-Route Controls

| Control | Recorded value |
|---|---|
| Static scanner root | `services/research/finrl` |
| Dynamic training probe | `finrl_stub_training_step` in `tests/integrations/test_research_no_order_route.py` |
| Broker outbox | empty |
| `governance_boundary.order_routing_enabled` | false |
| `governance_boundary.broker_session_enabled` | false |
| `governance_boundary.capital_binding` | false |
| `governance_boundary.deployment_stage` | `none` |
| `admission_packet.safety_assertions.no_order_route` | true |
| `admission_packet.downstream_scope.order_route` | `none` |

The runtime smoke also records the real backend attempt as explicit dependency
failure in this workspace:

| Field | Recorded value |
|---|---|
| `backend` | `finrl_ppo` |
| `status` | `dependency_or_config_error` |
| `cause_type` | `ModuleNotFoundError` |
| `cause_message` | `No module named 'finrl'` |
| `silent_stub_fallback` | false |

This is fail-closed evidence. The missing upstream package is recorded directly
and is not converted into a silent stub success.

## Registry Admission Boundary

The `OSS-FINRL-V2-001` admission packet is only a registry candidate-review
packet:

| Admission field | Recorded value |
|---|---|
| `schema_version` | `PromotionReadinessPacket.v1` |
| `registry_request.current_artifact_state` | `draft` |
| `registry_request.requested_artifact_state` | `candidate` |
| `registry_request.approval_scope` | `candidate_admission_review_only` |
| `registry_request.registry_write_authority` | `registry_service_only` |
| `registry_request.registry_write_performed` | false |
| `registry_request.deployment_stage` | `none` |
| `downstream_scope.registry_admission_packet_only` | true |
| `downstream_scope.broker_session_opened` | false |
| `downstream_scope.order_route` | `none` |
| `downstream_scope.capital_binding` | `none` |

Even when the FinRL packet reports `can_proceed=true`, that means only that the
review packet is internally complete for candidate review. It does not perform a
registry mutation and does not open paper, canary, live, broker, or capital
routes.

## Output Boundary

FinRL may produce only research / registry-review artifacts:

- `model_artifact`
- `evaluation_result`
- `candidate_packet`
- `registry_admission_packet`

The proof explicitly excludes:

- orders or order routes
- broker sessions
- runtime bindings
- deployment-stage mutation
- capital binding
- direct governance or registry writes from the FinRL adapter

## Verification

Focused verification for this proof lives in
`tests/governance/test_rl_proof_artifacts.py`.

Expected command:

```bash
pytest -q tests/governance/test_rl_proof_artifacts.py
```
