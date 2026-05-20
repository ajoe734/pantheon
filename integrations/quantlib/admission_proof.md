# QuantLib Admission Proof

Task: `RES-ACT-QUANTLIB-001-V2`
Owner: `Codex`
Reviewer: `Gemini`
Status: adapter-specific candidate admission artifact

## Scope

This packet records QuantLib's adapter-specific candidate admission proof for
the retained TXO option-chain `pricing_snapshot` artifact. It composes the
generic `ProductionDataProof.v1` evidence boundary with the reviewed
`PromotionReadinessPacket.v1` emitted by `OSS-QUANTLIB-V2-001`.

The packet admits the QuantLib pricing snapshot to registry candidate review
only. It performs no registry write, opens no broker session, routes no order,
binds no capital, and grants no paper/canary/live deployment authority.

## Evidence Inputs

| Evidence | Path |
|---|---|
| QuantLib production admission packet | `support/evidence/OSS-QUANTLIB-V2-001/admission_packet.json` |
| QuantLib pricing snapshot | `support/evidence/OSS-QUANTLIB-V2-001/pricing_snapshot.json` |
| QuantLib closeout | `support/evidence/OSS-QUANTLIB-V2-001/closeout.md` |
| Reviewer approval | `support/reviews/OSS-QUANTLIB-V2-001-review-codex2.md` |
| Pricing evidence proof | `integrations/quantlib/pricing_evidence_retention.md` |
| Pricing snapshot builder | `services/research/quantlib/production_option_chain.py` |
| Admission packet emitter | `services/research/quantlib/registry_admission_packet.py` |

## Admission Summary

| Field | Value |
|---|---|
| Source packet schema | `PromotionReadinessPacket.v1` |
| Source task | `OSS-QUANTLIB-V2-001` |
| Target type | `artifact` |
| Target artifact | `quantlib-production-option-chain-txo-2.0.0` |
| Artifact type | `pricing_snapshot` |
| Artifact state before admission | `draft` |
| Requested transition | `draft_to_candidate` |
| Deployment stage | `none` |
| Environment | `paper` review scope only |
| Production data proof | `pdp-quantlib-pricing-snapshot-20260517` |
| Dataset ref | `dataset:txo-option-chain-fixture-2026-05` |
| StrategySpec | `quantlib-txo-option-chain-pricing-spec-v1` |
| Source run | `pricing-snapshot:quantlib_production_option_chain:2.0.0` |
| Pricing snapshot checksum | `sha256:80b1a323b3ce1f3fa5bdb35e20b8750e7c14c3d97fe7b06c36335ea205095b59` |
| Missing evidence | none |
| Admission result | `can_proceed=true` for candidate review only |

## Admission Gates

| Gate | Result |
|---|---|
| TXO chain contract floor | passed: 5 strikes, 3 expiries, 30 contracts |
| Greeks completeness | passed: `price`, `delta`, `gamma`, `vega`, and `theta` on each retained row |
| Pricing snapshot projection | passed: draft `pricing_snapshot` with registry id and checksum |
| Lineage refs | passed: dataset refs, StrategySpec id, and source run id present |
| Safety fail-closed | passed: registry write false, deployment stage none, order route none |

These metrics are admission evidence for review. They are not a trading
authorization and do not bypass later registry, approval, paper, canary, or live
gates.

## No-Order-Route Boundary

The QuantLib packet is constrained to the same F3 output boundary as the
research activation proof:

- produced artifact types: `pricing_snapshot`, `evaluation_result`,
  `registry_admission_packet`, `candidate_packet`
- execution targets: `research`, `registry_review`
- attempted mutation types: none
- static adapter surface: `services/research/quantlib`
- dynamic replay output: pricing snapshot rows, Greeks, checksum, and registry
  projection only

Forbidden outputs remain:

- broker/order routes
- runtime bindings
- deployment-stage mutation
- capital binding
- direct registry writes

## Admission Decision

QuantLib pricing evidence is sufficient to request registry candidate review for
the draft pricing snapshot named above. The request remains review-only:

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
`tests/governance/test_quantlib_proof_artifacts.py`.

Expected command:

```bash
pytest -q tests/governance/test_quantlib_proof_artifacts.py
```
