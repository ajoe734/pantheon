# QuantLib Pricing Evidence Retention

Task: `RES-ACT-QUANTLIB-001-V2`
Owner: `Codex`
Reviewer: `Gemini`
Status: adapter-specific pricing evidence artifact

## Scope

This proof maps the reviewed QuantLib TXO option-chain pricing snapshot into the
generic `ProductionDataProof.v1` shape delivered by `RES-ACT-001-V2`.

It proves that the QuantLib research adapter has a retained, checksummed,
point-in-time pricing evidence bundle for offline pricing review and registry
candidate admission. It is not a registry write, broker route, runtime binding,
deployment-stage change, live-market-data claim, or capital-binding claim.

The source data boundary is the governed TXO option-chain fixture used by
`OSS-QUANTLIB-V2-001`; it does not claim broker-feed correctness or intraday
execution readiness.

## Evidence Inputs

| Evidence | Path |
|---|---|
| QuantLib pricing snapshot | `support/evidence/OSS-QUANTLIB-V2-001/pricing_snapshot.json` |
| QuantLib admission packet | `support/evidence/OSS-QUANTLIB-V2-001/admission_packet.json` |
| Production option-chain pricer | `services/research/quantlib/production_option_chain.py` |
| Admission packet emitter | `services/research/quantlib/registry_admission_packet.py` |
| Reviewer approval | `support/reviews/OSS-QUANTLIB-V2-001-review-codex2.md` |
| Closeout record | `support/evidence/OSS-QUANTLIB-V2-001/closeout.md` |

The source dataset ref is `dataset:txo-option-chain-fixture-2026-05`. The
retained snapshot records a TXO-like chain with 5 strikes, 3 expiries, 15 calls,
15 puts, and per-contract price, delta, gamma, vega, and theta.

## ProductionDataProof Mapping

| Field | QuantLib value |
|---|---|
| `schema_version` | `ProductionDataProof.v1` |
| `proof_id` | `pdp-quantlib-pricing-snapshot-20260517` |
| `activation_tier` | `R3` |
| `adapter_kind` | `quantlib` |
| `adapter_id` | `quantlib-pricing-evidence-retention-20260517` |
| `source_dataset_refs` | `dataset:txo-option-chain-fixture-2026-05` |
| `provider.name` | `Pantheon governed TXO option-chain fixture` |
| `provider.source_class` | `production_research_fixture` |
| `provider.dataset_id` | `txo-option-chain-fixture-2026-05` |
| `entitlement.entitlement_ref` | `ENT-QUANTLIB-TXO-PRICING-RESEARCH-2026-05-17` |
| `entitlement.license_scope` | `internal_research` |
| `entitlement.allowed_use` | `research`, `evaluation`, `registry_review` |
| `freshness.status` | `fresh` |
| `freshness.as_of` | `2026-05-17T00:00:00Z` |
| `freshness.last_ingested_at` | `2026-05-17T00:00:00Z` |
| `point_in_time.event_time_field` | `inputs.chain_definition.as_of` |
| `point_in_time.available_time_field` | `generated_at` |
| `point_in_time.source_watermark` | `2026-05-17T00:00:00Z` |
| `storage.backend` | `git_json_artifact` |
| `storage.dataset_ref` | `dataset:txo-option-chain-fixture-2026-05` |
| `storage.snapshot_ref` | `support/evidence/OSS-QUANTLIB-V2-001/pricing_snapshot.json` |
| `storage.checksum` | `sha256:80b1a323b3ce1f3fa5bdb35e20b8750e7c14c3d97fe7b06c36335ea205095b59` |
| `audit.evidence_bundle_ref` | `support/evidence/OSS-QUANTLIB-V2-001/` |
| `audit.ingest_run_id` | `OSS-QUANTLIB-V2-001:emit_pricing_snapshot` |
| `audit.normalization_run_id` | `quantlib-production-option-chain:2.0.0` |
| `audit.rate_limit_policy_ref` | `not-applicable-deterministic-fixture` |
| `no_order_route.produced_artifact_types` | `pricing_snapshot`, `evaluation_result`, `registry_admission_packet`, `candidate_packet` |
| `no_order_route.execution_targets` | `research`, `registry_review` |

## Pricing Gates

| Gate | Result |
|---|---|
| Contract floor | 30 contracts, covering 5 strikes x 3 expiries x call/put |
| Greek coverage | Every retained row includes `price`, `delta`, `gamma`, `vega`, and `theta` |
| Checksum retention | Snapshot, registry entry, admission summary, and snapshot ref use the same `sha256:` checksum |
| Lineage | Dataset ref, source run id, and StrategySpec id are present |
| Artifact state | Retained registry projection remains `draft` |
| Deployment stage | `deployment_summary.current_stage=none` |
| Compute scope | CPU-only; GPU not required |

## Output Boundary

QuantLib may produce only research and registry-review artifacts from this proof:

- `pricing_snapshot`
- `evaluation_result`
- `registry_admission_packet`
- `candidate_packet`

The proof explicitly excludes:

- orders or order routes
- broker sessions
- runtime bindings
- paper, canary, or live deployment-stage mutation
- capital binding
- direct governance or registry writes from the QuantLib adapter

The registry service remains the only write authority for artifact-state
transition. QuantLib may request `draft -> candidate` review through an admission
packet, but it cannot advance its own artifact state.

## Verification

Focused verification for this proof lives in
`tests/governance/test_quantlib_proof_artifacts.py`.

Expected command:

```bash
pytest -q tests/governance/test_quantlib_proof_artifacts.py
```
