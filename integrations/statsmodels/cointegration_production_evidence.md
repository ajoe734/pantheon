# statsmodels Cointegration Production Evidence

Task: `RES-ACT-STAT-001-V2`
Owner: `Codex`
Reviewer: `Gemini`
Status: adapter-specific proof artifact

## Scope

This proof maps the reviewed statsmodels production cointegration evidence into
the generic `ProductionDataProof.v1` shape delivered by `RES-ACT-001-V2`.

It proves that the statsmodels cointegration adapter has a production-scale,
entitled, point-in-time, durably stored TWSE/TPEx OHLCV dataset for offline
Engle-Granger research and registry review. It is not a registry write,
deployment-stage change, broker route, runtime binding, or capital-binding
claim.

## Evidence Inputs

| Evidence | Path |
|---|---|
| statsmodels production admission packet | `support/evidence/OSS-STAT-V2-001/admission_packet.json` |
| statsmodels production closeout | `support/evidence/OSS-STAT-V2-001/closeout.md` |
| Production dataset manifest | `support/evidence/MGMT-QLIB-001/dataset_manifest.json` |
| Production cointegration runner | `services/research/statsmodels/production_cointegration.py` |
| statsmodels admission packet emitter | `services/research/statsmodels/registry_admission_packet.py` |
| statsmodels production tests | `services/research/statsmodels/test_production_cointegration.py` |
| statsmodels governance overlay | `integrations/statsmodels/governance.md` |

The source dataset ref is
`dataset:tw-equity-ohlcv-top50-2024-daily`. The dataset manifest records 50
TWSE/TPEx instruments, daily OHLCV fields, and 2.0096 years of history from
2024-01-02 through 2026-01-05.

## ProductionDataProof Mapping

| Field | statsmodels value |
|---|---|
| `schema_version` | `ProductionDataProof.v1` |
| `activation_tier` | `R3` |
| `adapter_kind` | `statsmodels` |
| `adapter_id` | `statsmodels-cointegration-production-evidence-20260105` |
| `proof_id` | `pdp-statsmodels-cointegration-twse-20260105` |
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
| `audit.audit_ref` | `support/evidence/OSS-STAT-V2-001/admission_packet.json` |
| `audit.ingest_run_id` | `ingest-twse-openapi-2026-01-05-001` |
| `audit.normalization_run_id` | `norm-tw-equity-ohlcv-2026-01-05-001` |
| `audit.rate_limit_policy_ref` | `policy:twse-openapi-rate-limit-v1` |
| `no_order_route.produced_artifact_types` | `signal_snapshot`, `registry_admission_packet`, `candidate_packet` |
| `no_order_route.execution_targets` | `research`, `registry_review` |

## Cointegration Data Gates

| Gate | Result |
|---|---|
| Instrument floor | 50 instruments, meeting the >=50 floor |
| History floor | 2.0096 years, meeting the >=2 year floor |
| Daily period floor | 525 minimum periods per instrument, meeting the >=504 floor |
| OHLCV field coverage | `open`, `high`, `low`, `close`, `volume` |
| Market scope | Taiwan, TWSE + TPEx |
| Entitlement | research and model training allowed; no order-capable allowed use |
| PIT evidence | `date` event time plus `ingestion_timestamp` available time |
| Durable storage | object-store snapshot with `sha256:` checksum |
| Audit trail | ingest, normalization, evidence bundle, and rate-limit refs present |
| Rolling window | 504 daily periods |
| Pair universe | 10 TWSE large-cap pairs |
| Cointegration gate | 10 of 10 pairs below p<0.05 |
| Best pair | `TWSE_0004/TWSE_0044` with `best_p_value=0.0055868352` |
| Signal snapshot checksum | `sha256:7f7049632dc13a004e88dfd484832389495c3a2c2172d2035b29ef89d94a0a7b` |

The point-in-time claim is limited to the governed daily OHLCV dataset used for
offline cointegration research. It does not claim live broker feed correctness,
intraday execution readiness, or online signal routing.

## Adapter Evidence Instance

| Field | Recorded value |
|---|---|
| `adapter_evidence.adapter_id` | `statsmodels-cointegration-production-evidence-20260105` |
| `adapter_evidence.adapter_kind` | `statsmodels` |
| `adapter_evidence.backend` | `statsmodels_production_cointegration` |
| Dataset manifest | `support/evidence/MGMT-QLIB-001/dataset_manifest.json` |
| Admission packet | `support/evidence/OSS-STAT-V2-001/admission_packet.json` |
| Runner | `services/research/statsmodels/production_cointegration.py` |
| Packet emitter | `services/research/statsmodels/registry_admission_packet.py` |
| Candidate artifact | `signal_snapshot` |
| Candidate registry id | `statsmodels-production-cointegration-tw-cross-sectional-equity-alpha-2.0.0` |
| Candidate artifact state | `draft` |
| Registry write performed | `false` |
| Order route | `none` |

## Output Boundary

statsmodels may produce only research and registry-review artifacts from this
proof:

- `signal_snapshot`
- `registry_admission_packet`
- `candidate_packet`

The proof explicitly excludes:

- orders or order routes
- broker sessions
- runtime bindings
- deployment-stage mutation
- capital binding
- direct governance or registry writes from the statsmodels adapter

The registry service remains the only write authority for artifact-state
transition. statsmodels may request `draft -> candidate` review through an
admission packet, but it cannot advance its own artifact state.

## Verification

Focused verification for this proof lives in
`tests/governance/test_statsmodels_proof_artifacts.py`.

Expected command:

```bash
pytest -q tests/governance/test_statsmodels_proof_artifacts.py
```
