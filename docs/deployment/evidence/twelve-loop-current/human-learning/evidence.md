# Deployed E2E Evidence: Agora, Imitation, and Consultation (Loops 5, 6, 7)

- **Task ID**: `L12-CURRENT-E2E-HUMAN-LEARNING-20260814`
- **Owner**: `Antigravity2`
- **Reviewer**: `Claude2`
- **Phase**: `W4-e2e`
- **Status**: `ready_for_independent_review`
- **Target Branch**: `task/L12-CURRENT-E2E-HUMAN-LEARNING-20260814` -> `dev`

## 1. Summary of Scope & Verification

This task delivers deployed end-to-end verification covering Loops 5, 6, and 7:
1. **Loop 5: Agora Interaction Evidence & Durable Dataset Handoff**
   - User/Operator interaction evidence submission (`POST /bff/agora/interaction-evidence`)
   - Leased dataset worker inbox processing into `DatasetVersion` and durable handoff (`POST /bff/agora/dataset-worker/process`)
   - Agora handoff drainer (`process_drainer_cycle`) claiming pending handoffs from Agora BFF (`GET /internal/agora/dataset-handoffs`), posting to Policy Learning (`POST /api/policy-learning/agora-handoff`), and acknowledging on Agora BFF (`POST /internal/agora/dataset-handoffs/{handoff_id}/ack`)
   - Exact `handoff_id` and `dataset_version_id` preserved in Policy Learning candidate records
   - Idempotent replay and anti-zero-candidate assertion.

2. **Loop 6: Human Imitation / Shadow Evaluation & Research HTTP Handoff**
   - Candidate processing and HTTP handoff to Research owner authority (`POST /api/policy-learning/candidates/{candidate_id}/handoff` invoking `POST /api/research-orchestrator/intake/imitation-candidate`)
   - Exact readback from Research owner authority (`GET /api/research-orchestrator/runs/{experiment_run_id}`) verifying `task_id`, `run_id`, and `candidate_id`
   - Replay is idempotent across repeated submissions
   - Anti-direct-store verification: confirms absence of in-process cross-service store imports (`ResearchOrchestratorStore`, `intake_imitation_candidate`).

3. **Loop 7: Consultation Provider Interaction & Governance Handoff**
   - Operator submits ConsultRequest to Consultation API (`POST /api/consult/requests` + `/submit`)
   - Workflow executor (`run_tick`) claims request, obtains contribution from OpenClaw provider endpoint over HTTP, publishes `ConsultMemo`, and forwards `ConsultGateHandoff` to Governance handoff sink
   - Governance sink acknowledges handoff and records exact `request_id`, `memo_id`, `handoff_id`, and target gate
   - Functional health and DLQ degradation upon missing downstream sink; DLQ replay restores health to ok
   - Replay recovery over acknowledged handoff reuses existing record with zero duplicate OpenClaw turns.

4. **Cross-Loop Human-Learning Chain Correlation**
   - Validates end-to-end correlation across Agora Evidence -> DatasetVersion -> Policy Learning Candidate -> Research Run -> Consultation Request -> Published Memo -> Governance Handoff.

## 2. Executed Validation Suite

```bash
# 1. Deployed E2E suite
.venv-pantheon/bin/python -m pytest -v tests/integration/l12/test_current_human_learning_deployed_e2e.py

# 2. Combined component and integration suite (20 tests)
.venv-pantheon/bin/python -m pytest -v tests/integration/l12/test_current_human_learning_deployed_e2e.py \
  services/consultation/tests/test_current_provider_handoff.py \
  services/policy-learning/tests/test_current_agora_handoff_cutover.py \
  services/policy-learning/tests/test_current_research_http_handoff.py \
  services/governance/test_consultation_handoff.py
```

Result: `20 passed, 1 warning in 17.89s` (All 20 tests PASSED).
