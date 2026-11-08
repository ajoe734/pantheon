# Evolution Isolated Stimulus Acceptance Runbook (`EVOLUTION-ISOLATED-STIMULUS-20261107`)

## 1. Operator Authorization and Evidence Level

- **Plan Reference:** `/tmp/pantheon-evolution-simulation-20261107/PLAN.md`
- **Explicit Operator Instruction (2026-11-07):** `"模擬一筆可以促發的 來把後續跑完"` — authorizes one isolated synthetic acceptance case and its full downstream test flow through qualification, Governance approval, durable outbox dispatch, and Research Orchestrator retrain execution.
- **Evidence Level:** `local_integration_simulation` (`natural_market_qualification_proven: false`).
- **Historical Truth Preserved:** This simulation supersedes the earlier natural-only restriction for this simulation branch only; it does **not** prove natural-market AC5 qualification in paper trading (`EVOLUTION-RESEARCH-HOSTED-ACCEPT-20261104` natural qualification remains `unproven_in_natural_paper_trading`, and historical run `rrun-20261107-022` is untouched).

## 2. Safe Coordinator Invocation

From a clean Pantheon checkout (with the repo Python environment):

```bash
python3 scripts/run_evolution_program_simulation.py \
  --output docs/deployment/evidence/EVOLUTION-ISOLATED-STIMULUS-20261107/evidence.json
```

To run the focused regression test suite (covering fail-closed refusal guards, the `candidate_created` end-to-end simulation path, and the `no_candidate` variant):

```bash
pytest scripts/test_run_evolution_program_simulation.py -v
```

## 3. Isolation & Fail-Closed Guardrails

`scripts/run_evolution_program_simulation.py` enforces `validate_simulation_config()` before any server or store is initialized:

1. **Broker & Capital Refusal (`broker_capital_refused`):** Any attempt to enable broker orders or capital mutations fails closed immediately.
2. **Shared Policy Immutability (`shared_policy_mutation_refused`):** `services/evolution/config/program_qualification_policy.json` is verified by SHA-256 (`b3571f07f3fd05cb2515f3c33c7cf51fea36cf6bf80ba7519c6f767e482bdf5b`) before and after the run (`allow_test_scope: false` remains untouched).
3. **Synthetic Tenant Only (`non_synthetic_tenant_refused`):** Only a tenant containing `sim` or `synthetic` (default `tenant-isolated-sim-20261107`) is accepted; `default`, `dev`, `staging`, `prod`, and regular tenant names are refused.
4. **Synthetic Data Scope Only (`non_synthetic_data_scope_refused`):** `data_scope` must be `"synthetic"` or `"test"`; synthetic records are never relabeled as regular or natural.
5. **Paper Stage & Mode Only (`non_paper_stage_refused` / `non_paper_mode_refused`):** Non-paper deployment stages (`canary`, `live`) or execution modes are refused.
6. **Loopback Ephemeral Target Only (`non_local_target_refused`):** Non-loopback hosts (`127.0.0.1` / `localhost` only) are refused; every in-process owner service binds to an ephemeral `127.0.0.1:0` socket.
7. **Temporary Store Isolation (`shared_state_path_refused`):** Shared `/tmp/pantheon/...` and repository service data paths are refused; all owner stores live under `tempfile.mkdtemp(prefix="pantheon-evolution-sim-")` and are removed on exit.

## 4. Exact Policy Difference (Shared vs. Isolated Copy)

| Field | Shared Policy (`services/evolution/config/program_qualification_policy.json`) | Isolated Simulation Copy (`<temp_root>/program_qualification_policy.simulation.json`) |
| :--- | :--- | :--- |
| `policy_version` | `"2026-11-05.1"` | `"2026-11-05.1-isolated-simulation-20261107"` |
| `allow_test_scope` | `false` | `true` |
| `min_distinct_breach_days` | `3` | `3` (unchanged) |
| `Persistence lookback_days` | `30` | `30` (unchanged) |
| `metric_allowlist` | `["rolling_drawdown_multiple"]` | `["rolling_drawdown_multiple"]` (unchanged) |
| `target_stages` | `["paper"]` | `["paper"]` (unchanged) |

In Step 2a of the simulation, running `ProgramQualifier` with the unmodified shared policy (`2026-11-05.1`) against the synthetic target proves that `real_target` fails closed (`reason: "persona data_scope synthetic is refused"`, `qualified: 0`, `drafted: 0`).

## 5. End-to-End Recorded Flow (`docs/deployment/evidence/EVOLUTION-ISOLATED-STIMULUS-20261107/evidence.json`)

1. **Upstream Synthetic Stimulus (`data_scope="synthetic"`):**
   - Seeds isolated Persona `persona-sim-isolated-20261107`, Registry strategy artifact `artifact-sim-isolated-20261107-v1` (`1.0.0`), and Telemetry runtime summary `rt-sim-isolated-20261107` across three synthetic mark dates (`2026-11-06`, `2026-11-07`, `2026-11-08`) with `drawdown_pct=0.07` vs. `expected_drawdown=0.05` (`rolling_drawdown_multiple=1.40 > 1.25`).
   - Runs `services/reconciliation-drift` scheduled reconcile + drift report and `DriftReportIncidentConsumer` to persist 3 breached evaluations (`rdeval-...`) and 1 incident (`inc-drift-db1ab5760150`).
   - Runs `POST /api/evolution/daily-sweep` to create `EvolutionDecision` `evo-sweep-inc-drift-db1ab5760150` (`action_type="retrain"`, `metric_name="rolling_drawdown_multiple"`).
2. **Program Qualification & Pre-Approval Stop:**
   - With the isolated policy copy (`2026-11-05.1-isolated-simulation-20261107`), all 6 criteria (`real_threshold_signal`, `real_target`, `persistent_breach` with `distinct_breach_days=3`, `fresh_evidence`, `executable_action`, `no_open_duplicate`) pass.
   - Drafts 1 `EvolutionProgram` (`origin="auto_qualified"`, `status="under_review"`) and stops before dispatch (`decision_state="proposed"`, `outbox_record_count=0`, `research_run_count=0`).
3. **Negative Governance Proofs:**
   - Missing `approval_id` (`409 approval_missing`), mismatched program binding (`409 approval_program_mismatch`), already-decided rejected approval (`409 approval_already_decided`), pending undecided approval (`409 approval_pending`), and proposer self-decide (`403`) are all refused with zero outbox or research runs started.
4. **Real Isolated Governance Approval & Outbox Preparation:**
   - Synthetic test proposer (`sim-human-operator-proposer-20261107`) files the exact `approval-binding` request on the isolated Governance service, attaches it via `submit_evolution_review`, and synthetic test reviewer (`sim-human-governance-reviewer-20261107`, role `governance_reviewer`) reviews and decides `approved` (`audit_event_count=3`).
   - `POST /api/evolution/programs/{program_id}/actions/approve_program` verifies the Governance decision over HTTP, activates the program (`status="active"`), advances `evo-sweep-inc-drift-db1ab5760150` to `approved`, and prepares outbox record `evo-dispatch-b619f5a9-979b-5fb9-99d6-91a134c6bad7` (`status="pending"`).
5. **Dispatch Worker, Real Research Retrain Executor & Receipt Convergence:**
   - `dispatch_worker.run_poll` (poll 1) dispatches the outbox intent to `services/research/main.py` (`_execute_retrain`), creating task `rtask-20261108-001` and run `rrun-20261108-001`.
   - `_execute_retrain` opens Training Session `trn-...` and runs real local `vectorbt` preview evaluations for baseline (`lookback_bars=3`, `sharpe_ratio=1.860738`) and mutated candidate (`lookback_bars=2`, `sharpe_ratio=2.704302`), passes the Teaching evaluation gate, and registers candidate child `artifact-sim-isolated-20261107-v1-retrain-c357d4eb53` (`version="1.1.0"`, `artifact_state="candidate"`, `outcome="candidate_created"`).
   - `dispatch_worker.run_poll` (poll 2) verifies the terminal receipt from Research Orchestrator, links `rrun-20261108-001` to the program, marks `evo-sweep-inc-drift-db1ab5760150` `executed` (`execution_result.status="succeeded"`), and marks the outbox record `published`.
   - Zero runtime bindings, deployment plans, capital records, or broker orders are modified or created.
6. **Idempotency & Cleanup:**
   - Re-running `daily-sweep`, `approve_program` (with the same `idempotency_key`), and `dispatch_worker.run_poll` creates 0 duplicate decisions, 0 duplicate programs, 0 duplicate tasks, and 0 duplicate runs (`repeat_approve_program_http_status=200`, `repeat_approve_program_same_revision=true`).
   - The temporary directory is completely removed (`exists_after_cleanup=false`).
