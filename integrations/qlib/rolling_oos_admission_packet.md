# Qlib Rolling OOS Admission Packet

Task: `RES-ACT-QLIB-001-V2`
Owner: `Codex`
Reviewer: `Gemini`
Status: adapter-specific R5 admission artifact

## Scope

This packet records Qlib's adapter-specific R5 rolling out-of-sample admission
evidence. It composes the generic no-order-route OOS harness delivered by
`RES-ACT-004-V2` with the production rolling output from `OSS-QLIB-V2-001`.

The packet admits the Qlib rolling model artifact to registry candidate review
only. It performs no registry write, opens no broker session, routes no order,
binds no capital, and grants no paper/canary/live deployment authority.

## Evidence Inputs

| Evidence | Path |
|---|---|
| Production rolling admission packet | `support/evidence/OSS-QLIB-V2-001/admission_packet.json` |
| Production rolling closeout | `support/evidence/OSS-QLIB-V2-001/closeout.md` |
| Dataset manifest | `support/evidence/MGMT-QLIB-001/dataset_manifest.json` |
| StrategySpec packet | `support/evidence/MGMT-QLIB-002/strategy_spec_packet.json` |
| Rolling runner | `services/research/qlib/production_rolling_run.py` |
| Admission packet emitter | `services/research/qlib/registry_admission_packet.py` |
| Rolling runner tests | `services/research/qlib/test_production_rolling_run.py` |

## R5 Admission Summary

| Field | Value |
|---|---|
| Source packet schema | `PromotionReadinessPacket.v1` |
| Source task | `OSS-QLIB-V2-001` |
| Target type | `artifact` |
| Target artifact | `qlib-production-rolling-tw-cross-sectional-equity-alpha-2.0.0` |
| Artifact type | `model_artifact` |
| Artifact state before admission | `draft` |
| Requested transition | `draft_to_candidate` |
| Deployment stage | `none` |
| Environment | `paper` review scope only |
| Dataset manifest | `qlib-dataset-manifest:dataset-tw-equity-ohlcv-top50-2024-daily` |
| StrategySpec | `qlib-tw-cross-sectional-alpha-spec-v1` |
| Experiment run | `qlib-oos-a9a8f619c516` |
| Model artifact checksum | `sha256:625a860ef112938198ca9a22fb4e0b5e8e08fd4f8f5602fea10e2278b87c239b` |
| Missing evidence | none |
| Admission result | `can_proceed=true` for candidate review only |

## Rolling OOS Gates

| Gate | Result |
|---|---|
| Production dataset floor | passed: 50 instruments, 525 minimum periods per instrument |
| Rolling window count | passed: 457 windows |
| Mean rolling Sharpe | 0.115705 |
| Maximum rolling Sharpe | 7.147153 |
| Mean rolling IC | 0.005935 |
| Positive rolling Sharpe windows | 239 |
| Positive rolling IC windows | 223 |
| Model artifact projection | passed: draft `model_artifact` with checksum and storage ref |
| Lineage refs | passed: dataset refs, StrategySpec id, source run ids, and parent registry id present |
| Safety fail-closed | passed: registry write false, deployment stage none, order route none |

These metrics are admission evidence for review. They are not a trading
authorization and do not bypass later registry, approval, paper, canary, or live
gates.

## No-Order-Route Boundary

The R5 packet is constrained to the same output boundary as the generic OOS
harness:

- produced artifact types: `model_artifact`, `evaluation_result`,
  `registry_admission_packet`
- execution targets: `research`, `registry_review`
- attempted mutation types: none
- static adapter scan root: `services/research/qlib`
- dynamic replay output: rolling metric summary and model artifact refs only

Forbidden outputs remain:

- broker/order routes
- runtime bindings
- deployment-stage mutation
- capital binding
- direct registry writes

## Admission Decision

Qlib rolling OOS evidence is sufficient to request registry candidate review
for the draft model artifact named above. The request remains review-only:

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

## Verification

Focused verification for this packet lives in
`tests/governance/test_qlib_proof_artifacts.py`.

Expected command:

```bash
pytest -q tests/governance/test_qlib_proof_artifacts.py
```
