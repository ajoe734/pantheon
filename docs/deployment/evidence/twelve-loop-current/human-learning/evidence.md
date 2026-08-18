# Deployed E2E Evidence: Agora, Imitation, and Consultation (Loops 5, 6, 7)

- **Task ID**: `L12-GAP-F07-E2E-HUMAN-20260818`
- **Owner**: `Claude`
- **Reviewer**: `Antigravity2`
- **Phase**: `Pantheon 12-loop minimum functional closure 2026-08-18`
- **Status**: `ready_for_independent_review`
- **Target Branch**: `task/L12-GAP-F07-E2E-HUMAN-20260818` -> `dev`
- **Depends on / supersedes**: `L12-CURRENT-E2E-HUMAN-LEARNING-20260814` (same suite; this task closes
  GAP-F07 item 2 against it, see `docs/04/pantheon_twelve_loop_code_gap_2026-08-13/CURRENT_GAP_2026-08-18.md`)

## 1. What changed and why

`CURRENT_GAP_2026-08-18.md` GAP-F07 flagged that
`test_current_human_learning_deployed_e2e.py` was named "deployed" but was
actually in-process ASGI (`TestClient`/`uvicorn`), used `tmp_path` stores, and
used a `FakeOpenClawRiskProvider` instead of the real deployed OpenClaw
gateway adapter. This task rewrites the suite to be an opt-in
(`PANTHEON_L12_HUMAN_LEARNING_E2E=1`) deployed Compose E2E, following the
same house pattern already used by
`test_current_research_loops_deployed_e2e.py` and
`test_current_runtime_loops_deployed_e2e.py`:

1. **Loop 5: Agora Interaction Evidence & Durable Dataset Handoff**
   - Real `operator-bff` interaction-evidence submission
     (`POST /bff/agora/interaction-evidence`) and dataset-worker processing
     (`POST /bff/agora/dataset-worker/process`).
   - The durable handoff is drained by the **real**
     `policy-learning-shadow-eval-scheduler` Compose worker. The suite no
     longer calls `agora_handoff_drainer.process_drainer_cycle` in-process;
     it polls the worker's HTTP-visible effect
     (`GET /api/policy-learning/candidates`, `GET /internal/agora/dataset-handoffs`).
   - Exact `handoff_id` and `dataset_version_id` preserved in Policy
     Learning candidate records; idempotent replay via
     `POST /api/policy-learning/agora-handoff`.

2. **Loop 6: Human Imitation / Shadow Evaluation & Research HTTP Handoff**
   - `handoff_candidate_to_experiment_authority` and
     `post_imitation_candidate_intake_http` called against the real deployed
     `research-orchestrator-svc` URL (these are the production HTTP client
     functions, not a test shortcut).
   - Exact readback (`GET /api/research-orchestrator/runs/{run_id}`),
     idempotent replay, and anti-empty-candidate fail-closed assertion.

3. **Loop 7: Consultation Provider Interaction & Governance Handoff**
   - Real `consultation-svc` request submission, then the real
     `run_consultation_tick` executor invoked with `ExecutorConfig` pointed
     at the deployed Consultation API, the deployed
     `openclaw-gateway-adapter` consultation-contribution route (which in
     turn drives the real `openclaw agent` CLI against the OpenClaw
     gateway), and the deployed Governance `consultation-handoffs` sink.
   - No `FakeOpenClawRiskProvider`. Governance has no GET readback route for
     consultation handoffs, so the deployed Governance receipt is evidenced
     by the Consultation-side handoff `status == "acknowledged"` (which the
     executor only sets after Governance accepts the POST), not a store
     read.
   - Recovery proof: a second, independent `WorkflowStateStore` reclaims the
     acknowledged request and `execute_claim` reports a recovered outcome
     with zero duplicate OpenClaw turns.

4. **Same-run identity correlation**
   - `test_deployed_human_learning_chain_identity_correlation` asserts every
     identifier produced across the three cases (`evidence_id`,
     `dataset_version_id`, `agora_handoff_id`, `candidate_id`,
     `experiment_task_id`, `experiment_run_id`, `consult_request_id`,
     `memo_id`, `governance_handoff_id`) is present, without re-triggering
     any side effect.

5. **Anti-fixture guard**
   - `test_deployed_suite_has_no_fixture_or_product_store_shortcut` is an
     AST guard that fails the suite closed if `TestClient`, `uvicorn`,
     `unittest.mock`, `FakeOpenClawRiskProvider`, a direct
     `agora_handoff_drainer` import, or a `tmp_path`/`tmpdir`/`monkeypatch`
     fixture argument is reintroduced.

## 2. Executed validation

```bash
.venv-pantheon/bin/python -m pytest -v tests/integration/l12/test_current_human_learning_deployed_e2e.py
# -> 1 passed, 4 skipped (deployed cases skip: PANTHEON_L12_HUMAN_LEARNING_E2E is unset)

.venv-pantheon/bin/python -m pytest -q tests/integration/l12/
# -> 2 passed, 20 skipped

python3 -m pyflakes tests/integration/l12/test_current_human_learning_deployed_e2e.py
# -> clean

git diff --check
# -> exit 0
```

## 3. Residual risk (see `evidence.json` → `residual_risks.live_compose_run_not_exercised`)

This worker has no `docker compose` access and no OpenClaw credentials, so
the opt-in deployed path (`PANTHEON_L12_HUMAN_LEARNING_E2E=1`) itself was
**not** exercised against a live stack during this task. Only collection,
the default-skip path, and the AST anti-fixture guard were verified
locally. A future task with Compose + OpenClaw credential access must run
this suite live and record a `run-report.json` before this evidence is
treated as proof of a working live chain. This mirrors the same
not-yet-live-run status the sibling research/runtime deployed suites
already carry per `CURRENT_GAP_2026-08-18.md` §8.
