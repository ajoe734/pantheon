# TRL Preference Data Proof

Task: `RES-ACT-TRL-001-V2`
Owner: `Codex`
Reviewer: `Claude`
Status: adapter-specific proof artifact

## Scope

This proof maps the TRL bounded FB-002 preference dataset evidence into the
generic `ProductionDataProof.v1` shape delivered by `RES-ACT-001-V2`.

It is not a production registry write, deployment-stage change, broker route,
or capital-binding claim. The TRL output remains a non-executable preference
model handoff for offline evaluator / registry review only.

## Evidence Inputs

The proof is based on the already reviewed TRL runtime-data activation bundle:

| Evidence | Path |
|---|---|
| Evidence summary | `support/evidence/P2-TRL-RUNTIME-DATA-ACTIVATION-001/activation_evidence_summary.json` |
| FB-002 dataset snapshot | `support/evidence/P2-TRL-RUNTIME-DATA-ACTIVATION-001/fb002_evidence_snapshot.json` |
| Artifact bundle | `support/evidence/P2-TRL-RUNTIME-DATA-ACTIVATION-001/artifact_bundle.json` |
| Candidate packet | `support/evidence/P2-TRL-RUNTIME-DATA-ACTIVATION-001/candidate_packet.json` |
| Real backend attempt | `support/evidence/P2-TRL-RUNTIME-DATA-ACTIVATION-001/real_backend_attempt.json` |
| Review record | `support/reviews/P2-TRL-RUNTIME-DATA-ACTIVATION-001-codex-review.md` |

The source ref is `feedback-store://bounded-fixture/240`. It records a bounded
governed FB-002 preference event fixture, not a live broker or market-data route.

## ProductionDataProof Mapping

| Field | TRL value |
|---|---|
| `schema_version` | `ProductionDataProof.v1` |
| `activation_tier` | `R3` |
| `adapter_kind` | `trl` |
| `adapter_id` | `trl-preference-data-proof-20260501` |
| `source_dataset_refs` | `feedback-store://bounded-fixture/240` |
| `provider.name` | `Pantheon governed FB-002 feedback store` |
| `provider.source_class` | `production_research` |
| `provider.dataset_id` | `fb002-runtime-data-activation-20260501` |
| `entitlement.allowed_use` | `research`, `model_training`, `evaluation` |
| `point_in_time.event_time_field` | `created_at` |
| `point_in_time.available_time_field` | `created_at` |
| `storage.backend` | `feedback_store+object_store` |
| `audit.evidence_bundle_ref` | `support/evidence/P2-TRL-RUNTIME-DATA-ACTIVATION-001/` |
| `no_order_route.produced_artifact_types` | `model_artifact`, `evaluation_result`, `candidate_packet` |
| `no_order_route.execution_targets` | `research`, `registry_review` |

The point-in-time claim is limited to the preference-event dataset. The bounded
events are timestamped before pair construction and training, and the proof does
not claim market-label PIT correctness.

## Preference Data Gates

| Gate | Result |
|---|---|
| Governed FB-002 events | 240 |
| Valid preference pairs | 240 |
| Strategy families | 3: `equity_cross_sectional`, `macro_rotation`, `stat_arb` |
| Action distribution | `approve=80`, `edit=80`, `reject=80` |
| Operator count | 12 |
| Duplicate feedback event ids | 0 |
| Source feedback ids present | true |
| Artifact linkage complete | true |
| Required preflight gates | open for FB-002 volume and preference-pair volume |

The baseline preference metrics recorded in the activation summary are
`holdout_accuracy=0.6667` and `auc_roc=0.7167`. They are bounded evidence for
review, not permission for direct execution.

## Output Boundary

TRL may produce only research / registry-review artifacts:

- `model_artifact`
- `evaluation_result`
- `candidate_packet`

The proof explicitly excludes:

- orders or order routes
- broker sessions
- runtime bindings
- deployment-stage mutation
- capital binding
- direct governance or registry writes from the TRL adapter

The candidate packet requests only offline registry review. It keeps
`deployment_stage=none` and `current_artifact_state=draft`; any candidate
transition must still be performed by the registry service after admission
review.

## Verification

Focused verification for this proof lives in
`tests/governance/test_trl_proof_artifacts.py`.

Expected command:

```bash
pytest -q tests/governance/test_trl_proof_artifacts.py
```
