# statsmodels Admission Proof

Task: `RES-ACT-STAT-001-V2`
Owner: `Codex`
Reviewer: `Gemini`
Status: adapter-specific admission artifact

## Scope

This packet records the statsmodels production cointegration admission posture
against the generic `ProductionDataProof.v1` and research admission gate
delivered by `RES-ACT-001-V2`.

The packet admits the statsmodels `signal_snapshot` artifact to registry
candidate review only. It performs no registry write, opens no broker session,
routes no order, binds no capital, and grants no paper/canary/live deployment
authority.

## Evidence Inputs

| Evidence | Path |
|---|---|
| statsmodels production admission packet | `support/evidence/OSS-STAT-V2-001/admission_packet.json` |
| statsmodels production closeout | `support/evidence/OSS-STAT-V2-001/closeout.md` |
| Production dataset manifest | `support/evidence/MGMT-QLIB-001/dataset_manifest.json` |
| Production cointegration runner | `services/research/statsmodels/production_cointegration.py` |
| statsmodels admission packet emitter | `services/research/statsmodels/registry_admission_packet.py` |
| statsmodels production tests | `services/research/statsmodels/test_production_cointegration.py` |
| Production data evidence mapping | `integrations/statsmodels/cointegration_production_evidence.md` |

## Admission Summary

| Field | Value |
|---|---|
| Source packet schema | `PromotionReadinessPacket.v1` |
| Source task | `OSS-STAT-V2-001` |
| Target type | `artifact` |
| Target artifact | `statsmodels-production-cointegration-tw-cross-sectional-equity-alpha-2.0.0` |
| Artifact type | `signal_snapshot` |
| Artifact state before admission | `draft` |
| Requested transition | `draft_to_candidate` |
| Deployment stage | `none` |
| Environment | `paper` review scope only |
| Dataset manifest | `qlib-dataset-manifest:dataset-tw-equity-ohlcv-top50-2024-daily` |
| StrategySpec | `qlib-tw-cross-sectional-alpha-spec-v1` |
| Signal snapshot | `signal-snapshot:statsmodels_production_cointegration:2.0.0` |
| Signal snapshot checksum | `sha256:7f7049632dc13a004e88dfd484832389495c3a2c2172d2035b29ef89d94a0a7b` |
| Missing evidence | none |
| Admission result | `can_proceed=true` for candidate review only |

## Candidate Admission Gates

| Gate | Result |
|---|---|
| Production dataset floor | passed: 50 instruments, 525 minimum periods per instrument |
| Rolling Engle-Granger | passed: 10 pairs, 10 cointegrated pairs, 504-period rolling window |
| Best pair | `TWSE_0004/TWSE_0044` with `best_p_value=0.0055868352` |
| Signal snapshot projection | passed: draft `signal_snapshot` with checksum and inline storage ref |
| Lineage refs | passed: dataset refs, StrategySpec id, source run ids, and manifest id present |
| Safety fail-closed | passed: registry write false, deployment stage none, order route none |
| Production data proof | passed: `ProductionDataProof.v1`, `activation_tier=R3`, `adapter_kind=statsmodels` |

These metrics are admission evidence for review. They are not a trading
authorization and do not bypass later registry, approval, paper, canary, or live
gates.

## No-Order-Route Boundary

The statsmodels admission proof is constrained to the same research-only output
boundary as the generic production data proof:

- produced artifact types: `signal_snapshot`, `registry_admission_packet`,
  `candidate_packet`
- execution targets: `research`, `registry_review`
- attempted mutation types: none
- static adapter scope: `services/research/statsmodels`
- dynamic production output: `signal_snapshot` metrics and registry candidate
  refs only

Forbidden outputs remain:

- broker/order routes
- runtime bindings
- deployment-stage mutation
- capital binding
- direct registry writes

## Admission Decision

statsmodels production cointegration evidence is sufficient to request registry
candidate review for the draft `signal_snapshot` artifact named above. The
request remains review-only:

- `registry_write_authority=registry_service_only`
- `registry_write_performed=false`
- `deployment_stage=none`
- `broker_session_opened=false`
- `order_route=none`
- `capital_binding=none`
- `risk_owner_required=false`
- `operator_required=false`

Any later promotion beyond candidate review must be performed by the registry
and governance services under their own task packets.

## Fail-Closed Conditions

The admission proof fails closed if any of these fields drift:

| Field | Required value |
|---|---|
| `registry_request.current_artifact_state` | `draft` |
| `registry_request.requested_artifact_state` | `candidate` |
| `registry_request.deployment_stage` | `none` |
| `registry_request.registry_write_performed` | `false` |
| `downstream_scope.registry_admission_packet_only` | `true` |
| `downstream_scope.registry_write_performed` | `false` |
| `downstream_scope.broker_session_opened` | `false` |
| `downstream_scope.order_route` | `none` |
| `downstream_scope.capital_binding` | `none` |
| `safety_assertions.no_order_route` | `true` |
| `safety_assertions.no_registry_write` | `true` |
| `safety_assertions.deployment_stage_remains_none` | `true` |

## Verification

Focused verification for this proof lives in
`tests/governance/test_statsmodels_proof_artifacts.py`.

Expected command:

```bash
pytest -q tests/governance/test_statsmodels_proof_artifacts.py
```
