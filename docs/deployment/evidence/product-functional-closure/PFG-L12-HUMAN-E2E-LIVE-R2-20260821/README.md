# PFG-L12-HUMAN-E2E-LIVE-R2-20260821: Deployed Human Learning Recovery & Proof

This task recovers the post-merge dev deployment and hosted Human E2E proof for
Loops 5 through 7 following the false terminal closeout of PFG-L12-HUMAN-E2E-20260820.

## Objective & Deployment Truth

1. **Exact SHA Ancestry & Governed Promotion**:
   - Backend canonical target `bb83df12e3cec11de0f441850f08a179ddd7394a` (from `PFG-L12-HUMAN-E2E-20260820` / PR #5117) was merged to `origin/dev` and is a direct ancestor of dev `97945de7c5193baa9832f6c02674714d889577b9`.
   - Frontend canonical target `8b5a7bbe868f9e3a56a4ed7baf818b642d57ba74` (from `PFG-MGMT-AI-FE-ACTIONS-20260820` / PR #600) was merged to `origin/dev` and is a direct ancestor of dev `693d8612218e5ec6620c80ab7a16d3429e842f6c`.
   - Promotion workflow run: `32554078801` (`nonprod-deploy.yml`), gate run: `32555528892` (`pantheon-integration-gate.yml`), Pair ID: `98c7d8026ef9c396b211b9f34c716be15c0d22c2e55bca4fc0755a9405d38529`.

2. **Hosted Version & Deployment Readbacks**:
   - `GET https://pantheon-lupin-dev-bff.35.201.204.12.sslip.io/bff/version`:
     ```json
     {"service":"operator-bff","version":"0.2.0","source_commit_sha":"97945de7c5193baa9832f6c02674714d889577b9","commit":"97945de7c5193baa9832f6c02674714d889577b9","source_commit_known":true,"environment":"dev","config_posture":{"auth_stub":false,"auth_mode":"strict","dev_login_enabled":true,"mfa_required":true,"assistant_kernel_enabled":true,"trade_journey_reader_backend":"json","trade_journey_projection_schema":"trade_journey_projection"}}
     ```
   - `GET https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io/deployment.json`:
     ```json
     {"pairId":"98c7d8026ef9c396b211b9f34c716be15c0d22c2e55bca4fc0755a9405d38529","commit":"693d8612218e5ec6620c80ab7a16d3429e842f6c","bffCommit":"97945de7c5193baa9832f6c02674714d889577b9","deploymentState":"accepted"}
     ```

3. **Source Posture & Egress Guard Live Readback**:
   - `GET http://127.0.0.1:18097/readyz`: `ready: true`, `service: "pantheon-source-ingest"`, `provider_egress_attempted: false`, `source_search_posture.mode: "dev"`.
   - `pantheon-source-ingest` container: `PANTHEON_EXTERNAL_EGRESS=deny`.
   - `pantheon-source-ingest-scheduler` container: `SOURCE_INGEST_CONTROLLER_MODE=reconcile_only`, `SOURCE_INGEST_CONTROLLER_MAX_TICKS=0`.

4. **Human Learning Deployed E2E Proof (Loops 5 through 7)**:
   - Run `PANTHEON_L12_HUMAN_LEARNING_E2E=1` against live Compose services with automatic strict dev-login authentication without hardcoded credentials.
   - Loop 5: Agora Interaction Evidence -> Policy Learning Shadow Imitation Candidate.
   - Loop 6: Policy Learning Candidate -> Research Orchestrator Experiment Run.
   - Loop 7: Consultation Request -> Supervised Workflow Executor -> OpenClaw Advisory Memo -> Governance Gate Handoff.
   - Restart and replay idempotency verified across all loops without duplicate candidate or memo emission.

## Deployed Execution & Proof Summary (2026-08-22)

The complete Human Learning E2E test suite (`test_current_human_learning_deployed_e2e.py`) passed against the live deployment.

### Test Results (6 passed in 108.18s)
- `test_deployed_agora_interaction_evidence_identity_chain`: **PASSED**
- `test_deployed_imitation_research_handoff_identity_chain`: **PASSED**
- `test_deployed_consultation_governance_handoff_identity_chain`: **PASSED**
- `test_deployed_human_learning_chain_identity_correlation`: **PASSED**
- `test_deployed_source_posture_and_egress_readback`: **PASSED**
- `test_deployed_suite_has_no_fixture_or_product_store_shortcut`: **PASSED**

### Durable Identity Correlation Chain
- `evidence_id`: `ev-l12-hl-ca0d7c91e7`
- `dataset_version_id`: `dsv-f8e8c8877962d16a2b78e744`
- `agora_handoff_id`: `gh-548cb3ab684e61ce3f2d9533`
- `candidate_id`: `sic-f75c8789f3fc738e8d6c5c44d2ce320d`
- `experiment_task_id`: `rtask-exp-sic-f75c8789f3fc738e8d6c5c44d2ce320d`
- `experiment_run_id`: `rrun-exp-sic-f75c8789f3fc738e8d6c5c44d2ce320d`
- `consult_request_id`: `cr-l12-hl-ca0d7c91e7`
- `memo_id`: `mem-a80a5443702ea0decc57`
- `governance_handoff_id`: `gh-aa368b2f8fa94fdeaa09`

The full run report is preserved in [deployed-run.json](./deployed-run.json).
