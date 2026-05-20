# Qlib Production Data Proof

Task: `RES-ACT-QLIB-001-V2`
Owner: `Codex`
Reviewer: `Gemini`
Status: adapter-specific proof artifact

## Scope

This proof maps the reviewed Qlib TWSE/TPEx OHLCV dataset evidence into the
generic `ProductionDataProof.v1` shape delivered by `RES-ACT-001-V2`.

It proves the Qlib research adapter has a production-scale, entitled,
point-in-time, durably stored dataset for offline model training and registry
review. It is not a registry write, deployment-stage change, broker route,
runtime binding, or capital-binding claim.

## Evidence Inputs

| Evidence | Path |
|---|---|
| Dataset manifest | `support/evidence/MGMT-QLIB-001/dataset_manifest.json` |
| Qlib production rolling admission packet | `support/evidence/OSS-QLIB-V2-001/admission_packet.json` |
| Qlib rolling runner | `services/research/qlib/production_rolling_run.py` |
| Qlib registry admission emitter | `services/research/qlib/registry_admission_packet.py` |
| Existing activation packet | `integrations/qlib/activation_packet.md` |
| Qlib governance overlay | `integrations/qlib/governance.md` |

The source dataset ref is
`dataset:tw-equity-ohlcv-top50-2024-daily`. The dataset manifest records 50
TWSE/TPEx instruments, daily OHLCV fields, and 2.0096 years of history from
2024-01-02 through 2026-01-05.

## ProductionDataProof Mapping

| Field | Qlib value |
|---|---|
| `schema_version` | `ProductionDataProof.v1` |
| `activation_tier` | `R3` |
| `adapter_kind` | `qlib` |
| `adapter_id` | `qlib-production-data-proof-20260105` |
| `source_dataset_refs` | `dataset:tw-equity-ohlcv-top50-2024-daily` |
| `provider.name` | `TWSE-OpenAPI` |
| `provider.source_class` | `research_grade` |
| `provider.dataset_id` | `tw-equity-ohlcv-top50-2024-daily` |
| `entitlement.entitlement_ref` | `ENT-TWSE-OPENAPI-RESEARCH-2024-001` |
| `entitlement.license_scope` | `research_and_model_training` |
| `entitlement.allowed_use` | `model_training`, `research` |
| `freshness.status` | `fresh` |
| `freshness.as_of` | `2026-01-05T00:00:00Z` |
| `freshness.last_ingested_at` | `2026-01-05T02:00:00Z` |
| `point_in_time.event_time_field` | `date` |
| `point_in_time.available_time_field` | `ingestion_timestamp` |
| `point_in_time.source_watermark` | `2026-01-05T00:00:00Z` |
| `storage.backend` | `object_store` |
| `storage.dataset_ref` | `dataset:tw-equity-ohlcv-top50-2024-daily` |
| `storage.snapshot_ref` | `snapshot:tw-equity-ohlcv-top50-2024-daily-v1.0` |
| `storage.checksum` | `sha256:fda828e5504eb3b4ae962bd2982c62b5c7b9075f7d2aa44ab197077f02146662` |
| `audit.evidence_bundle_ref` | `evidence:tw-equity-ohlcv-top50-2024-daily-v1.0` |
| `audit.ingest_run_id` | `ingest-twse-openapi-2026-01-05-001` |
| `audit.normalization_run_id` | `norm-tw-equity-ohlcv-2026-01-05-001` |
| `audit.rate_limit_policy_ref` | `policy:twse-openapi-rate-limit-v1` |
| `no_order_route.produced_artifact_types` | `model_artifact`, `evaluation_result`, `registry_admission_packet`, `candidate_packet` |
| `no_order_route.execution_targets` | `research`, `registry_review` |

## Data Gates

| Gate | Result |
|---|---|
| Instrument floor | 50 instruments, meeting the >=50 floor |
| History floor | 2.0096 years, meeting the >=2 year floor |
| Daily period floor | 525 periods per instrument in the rolling run packet, meeting the >=504 floor |
| OHLCV field coverage | `open`, `high`, `low`, `close`, `volume` |
| Market scope | Taiwan, TWSE + TPEx |
| Entitlement | research and model training allowed; no order-capable allowed use |
| PIT evidence | `date` event time plus `ingestion_timestamp` available time |
| Durable storage | object-store snapshot with `sha256:` checksum |
| Audit trail | ingest, normalization, evidence bundle, and rate-limit refs present |

The point-in-time claim is limited to the governed daily OHLCV dataset used for
Qlib training and evaluation. It does not claim live broker feed correctness or
intraday execution readiness.

## Output Boundary

Qlib may produce only research and registry-review artifacts from this proof:

- `model_artifact`
- `evaluation_result`
- `registry_admission_packet`
- `candidate_packet`

The proof explicitly excludes:

- orders or order routes
- broker sessions
- runtime bindings
- deployment-stage mutation
- capital binding
- direct governance or registry writes from the Qlib adapter

The registry service remains the only write authority for artifact-state
transition. Qlib may request `draft -> candidate` review through an admission
packet, but it cannot advance its own artifact state.

## Verification

Focused verification for this proof lives in
`tests/governance/test_qlib_proof_artifacts.py`.

Expected command:

```bash
pytest -q tests/governance/test_qlib_proof_artifacts.py
```
