# Product Functional Closure Hosted Acceptance Report

- Task: `PFG-HOSTED-ACCEPT-20260820`
- Program: `pantheon-product-functional-closure-20260820`
- Verified at: `2026-08-28T06:19:20Z`
- Mode: `hosted`
- Overall Status: **FAILED**
- Frontend SHA: `c230fc76bef78fc297135152f2acba690314bb9d`
- Backend SHA: `dcb14231d29f08f1646a4ee962b83fd2d4b67560`
- Profile: `hosted-functional`

| Gate | Name | Status | Duration (ms) | Notes |
|---|---|---|---|---|
| `gate_01_manifest_exact_pair` | Live manifest and exact deployed pair | **FAILED** | 118.78 | [gate_01.auth_posture] BFF auth posture is not strict/non-stub: {'auth_stub': True, 'auth_mode': 'permissive', 'dev_login_enabled': True, 'mfa_required': True, 'assistant_kernel_enabled': True, 'trade_journey_reader_backend': 'postgres', 'trade_journey_projection_schema': 'trade_journey_projection'} |
| `gate_02_source_manual_only_readiness` | Source Ingestion manual-only mode and bounded readiness | **FAILED** | 164.23 | [gate_02.source_runtime_evidence_missing] --source-runtime-evidence is required and must be provided |
| `gate_03_paper_runtime_execution` | Paper execution bounded state and executable RuntimeBinding | **FAILED** | 69.91 | [gate_03.paper_runtime_evidence_missing] --paper-runtime-evidence is required and must be provided |
| `gate_04_authenticated_product_journeys` | Required authenticated product journeys | **FAILED** | 0.0 | [gate_04.missing_l12_evidence] --l12-evidence is required and must be provided |
| `gate_05_code_disposition_and_simplification` | Code disposition and dead owner removal | **FAILED** | 0.4 | [gate_05.task_id] code disposition task_id 'PFG-HOSTED-CURRENT-DEV-CLOSEOUT-20260828' != expected 'PFG-HOSTED-ACCEPT-20260820' |
| `gate_06_rollback_and_switch_safety` | Gate-before-switch deployment and rollback drill safety | **FAILED** | 0.0 | [gate_06.rollback_evidence_missing] --rollback-evidence is required and must exist |
