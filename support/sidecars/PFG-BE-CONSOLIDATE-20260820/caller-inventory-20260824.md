# PFG backend consolidation caller inventory — 2026-08-24

Task: `PFG-BE-CONSOLIDATE-20260820-SIDECAR-CALLER-INVENTORY`  
Parent: `PFG-BE-CONSOLIDATE-20260820`  
Audited baseline: `8ca38337dee63d8759967ff5d670eaf24b4f983b` (`origin/dev`)  
Inventory owner: `Codex`

## Boundary and decision rule

This sidecar is an inventory only. It changes no product runtime, deployment,
deletion, generated output, or canonical parent/task state. The parent owns all
implementation and deletion decisions.

Every disposition below uses the corrected design vocabulary:

- `retain`: a current product, deployment, operator, or proof caller exists.
- `replace_then_delete`: the target is obsolete, but its caller/schema/test
  contract must first move to the named replacement and pass the named proof.
- `delete`: no product/deployment caller remains and replacement proof already
  exists; tests dedicated only to the deleted behavior leave with it.
- `defer`: an active caller, another declared owner, or missing cutover proof
  makes removal unsafe in this parent.

Historical task packets, archived evidence, and review notes were treated as
evidence references, not runtime callers. No candidate below had a direct
reference in `.github/workflows/`; deployment callers are in Compose and the
Pantheon deployment scripts instead.

## Summary

| Candidate | Disposition | Decisive current truth |
| --- | --- | --- |
| `services/source_ingestion/scheduler_worker.py` | `delete` | Only its dedicated unit test imports it; the deployed legacy-named service runs `controller_worker` |
| `scripts/source_ingest_scheduler_once.py` | `retain` | Canonical bounded manual one-shot CLI with operator runbook and contract tests |
| Compose `source-ingest-scheduler` service | `retain` | Default durable Source controller; the service name is compatibility, not the old worker |
| Compose `source-ingest-agora-projector` job | `defer` | Selected and awaited by the current bounded source-refresh deployment path |
| Automatic Agora full discovery | `replace_then_delete` | Default scheduler already drains durable BFF handoffs, but the manual route/test branch still calls discovery |
| Exact-ref Agora authority operations | `retain` | Candidate resolution and durable handoff admission still register/read exact dataset versions |
| `candidate_experiment_handoff.py` | `retain` | Active HTTP-only Policy Learning to Research handoff |
| Active `lean_runtime` implementation | `retain` | Dynamic fleet, signal producer, services, scripts, and E2E suites import it |
| Static `pantheon-paper-runtime` topology | `defer` | Root topology forbids it, but `docker-compose.exec.yml` and split-topology validation still require it |
| `BoundedPaperStrategy` and `smoke_algorithm.py` | `retain` | Explicit proof/test fixtures; neither is the default product strategy |
| Legacy lifecycle JSON projection/read path | `defer` | Current Compose defaults writer to disabled relational mode and BFF reader to JSON; separate activation/retirement ownership applies |
| Stable loop catalog specification/owner fields | `retain` | BFF `/loop-inventory` reads them at runtime |
| Catalog runtime maturity/evidence/task claims | `replace_then_delete` | BFF intentionally ignores them, but schema and registry tests still require them |

## Detailed inventory records

### 1. Retired Source scheduler implementation

```yaml
path_or_symbol: services/source_ingestion/scheduler_worker.py
behavior: Recurring HTTP loop that POSTs /api/source-ingest/run-scheduled.
callers:
  - services/source_ingestion/tests/test_scheduler_worker.py (dedicated unit test only)
runtime_or_deploy_refs: []
replacement: services/source_ingestion/controller_worker.py
replacement_proof:
  - docker-compose.yml service source-ingest-scheduler commands python -m services.source_ingestion.controller_worker
  - services/source_ingestion/tests/test_controller_worker.py
  - services/source_ingestion/tests/test_controller_worker_manual_once.py
  - docs/deployment/evidence/product-functional-closure/PFG-SOURCE-MANUAL-ONCE-20260820/evidence.json
disposition: delete
validation:
  - Re-run the exact import/path scan after deleting the module and its dedicated test.
  - Keep /api/source-ingest/run-scheduled: controller_worker still calls that API boundary.
  - Run Source controller, Compose activation, and manual-one-shot tests.
```

The repository-wide live-source scan found no import, dynamic import, command,
workflow, Compose entry, or deployment script for this module. Mentions outside
its unit test are planning/history/review records. This is the only candidate
with sufficient zero-caller evidence for direct deletion by the parent.

### 2. Canonical Source manual one-shot

```yaml
path_or_symbol: scripts/source_ingest_scheduler_once.py
behavior: Runs exactly one canonical controller tick and returns terminal readback.
callers:
  - docs/operations/source-ingest-manual-one-shot.md
  - scripts/tests/test_source_ingest_scheduler_once.py
  - services/source_ingestion/tests/test_controller_worker_manual_once.py
runtime_or_deploy_refs:
  - Operator invocation documented as python3 scripts/source_ingest_scheduler_once.py
replacement: null
replacement_proof: The script already imports run_controller_once from controller_worker.
disposition: retain
validation:
  - scripts/tests/test_source_ingest_scheduler_once.py
  - services/source_ingestion/tests/test_controller_worker_manual_once.py
```

This file is not a second scheduler owner: it is a bounded operator entry point
over the same controller. Retiring the old recurring worker must not remove it.

### 3. Source Compose compatibility name and projection job

```yaml
path_or_symbol: docker-compose.yml::source-ingest-scheduler
behavior: Default-on durable Source controller under a legacy-compatible service key.
callers:
  - services/source_ingestion/test_compose_activation.py
  - tests/integration/test_product_functional_compose_contract.py
  - services/control-plane/bff/test_loop_inventory_read_model_contract.py
runtime_or_deploy_refs:
  - scripts/deploy_nonprod_vm.sh required worker manifest
  - source-ingest-agora-projector depends_on this service completing for bounded refreshes
replacement: null
replacement_proof: Command resolves to services.source_ingestion.controller_worker, not scheduler_worker.
disposition: retain
validation:
  - Resolve default and source-ingest-scheduler-profile Compose configs.
  - Assert one Source controller owner and no scheduler_worker command.
```

```yaml
path_or_symbol: docker-compose.yml::source-ingest-agora-projector
behavior: One-off source snapshot projection into BFF Agora JSON surfaces.
callers:
  - scripts/project_market_data_to_bff_agora_surfaces.py
  - services/source_ingestion/test_compose_activation.py
  - tests/integration/test_product_functional_compose_contract.py
  - scripts/test_source_ingest_deploy_diagnostics_contract.py
runtime_or_deploy_refs:
  - scripts/deploy_nonprod_vm.sh selects, waits for, diagnoses, and validates this job when the source-ingest-scheduler profile is selected
replacement: A source-owned projection/read contract that removes the deploy script selection, wait, and readback dependency.
replacement_proof: Missing on the audited baseline.
disposition: defer
validation:
  - Do not remove until both Compose config and deploy_nonprod_vm.sh have zero references and replacement readback passes.
```

The profile name is overloaded: selecting `source-ingest-scheduler` makes the
controller bounded (`MAX_TICKS=1`) and activates the projector. That does not
make the old Python scheduler module a deployment caller.

### 4. Agora authority: automatic discovery versus exact-ref authority

```yaml
path_or_symbol: services/policy-learning/main.py::discover_eligible_datasets; AgoraDatasetAuthority.list_dataset_versions; scheduler_worker.run_tick
behavior: Discovers all eligible Agora dataset versions when POST /api/policy-learning/shadow-eval-tick has no explicit dataset_refs.
callers:
  - services/policy-learning/main.py no-ref branch of /api/policy-learning/shadow-eval-tick
  - services/policy-learning/tests/test_policy_learning_shadow_eval_scheduler.py
  - services/policy-learning/tests/test_l12_imit_001_authority_and_recovery.py
  - services/policy-learning/tests/test_l12_imit_001_real_dataset_scheduling.py
  - services/policy-learning/tests/test_l12_imit_001_default_compose_loop.py
runtime_or_deploy_refs:
  - No scheduled production caller: scheduler_worker.main calls run_intake_cycle, not run_tick.
  - policy-learning-shadow-eval-scheduler is deployed, but drains the BFF handoff queue through agora_handoff_drainer.
replacement: services/policy-learning/agora_handoff_drainer.py plus POST /api/policy-learning/agora-handoff
replacement_proof:
  - services/policy-learning/tests/test_current_agora_handoff_cutover.py proves the scheduled main path has zero run_tick calls and drains/retries durable handoffs.
  - PFG-AGORA-JOURNEY-E2E-20260820 remains the parent-level end-to-end deletion gate.
disposition: replace_then_delete
validation:
  - Preserve explicit dataset_refs shadow evaluation unless the parent separately proves it obsolete.
  - Rewrite/remove tests that assert no-ref database discovery before deleting list_dataset_versions or run_tick.
  - Re-scan the route, scheduler main, Compose command, and all policy-learning tests.
```

```yaml
path_or_symbol: services/policy-learning/agora_dataset_authority.py::register_version,get_dataset_versions
behavior: Tenant-scoped exact DatasetVersion registration and lookup for admitted handoffs and candidate resolution.
callers:
  - services/policy-learning/main.py::resolve_candidate_dataset
  - services/policy-learning/main.py::receive_agora_handoff
  - services/policy-learning/main.py application authority construction
  - policy-learning authority, admission, scheduling, and recovery test suites
runtime_or_deploy_refs:
  - docker-compose.yml policy-learning-svc receives Agora dataset store DSN/schema settings
replacement: null
replacement_proof: Exact-ref operations are part of the durable handoff consumer, not the retired scanner.
disposition: retain
validation:
  - Agora handoff admission, exact-ref resolution, tenant isolation, and recovery tests.
```

The module cannot be deleted wholesale. The removable unit is the automatic
list-all discovery branch; exact-ref registration and lookup remain live.

### 5. Policy Learning to Research candidate handoff

```yaml
path_or_symbol: services/policy-learning/candidate_experiment_handoff.py
behavior: Sends processed candidates to the Research service through research_candidate_client HTTP calls and records terminal handoff identity.
callers:
  - services/policy-learning/main.py candidate processing path
  - POST /api/policy-learning/candidates/{candidate_id}/handoff
  - services/policy-learning/tests/test_current_imitation_entrypoint.py
  - services/policy-learning/tests/test_current_research_http_handoff.py
  - services/policy-learning/tests/test_l12_imit_001_candidate_handoff.py
  - tests/integration/l12/test_current_human_learning_deployed_e2e.py
runtime_or_deploy_refs:
  - policy-learning-svc to research-orchestrator-svc HTTP boundary
replacement: null
replacement_proof: test_current_research_http_handoff.py rejects direct Research store imports.
disposition: retain
validation:
  - Candidate auto-handoff, explicit route, failure-closed, retry, and deployed E2E tests.
```

### 6. `services/execution/lean_runtime/`

```yaml
path_or_symbol: services/execution/lean_runtime/{bootstrap_contract,executor,paper_runtime,paper_signal_producer,pending_signal_store,performance_telemetry,runtime_bootstrap,runtime_context,runtime_identity,signal_consumer,signal_producer,symbol_parser}.py
behavior: Active paper execution, binding isolation, runtime bootstrap, signals, telemetry, and symbol bridge.
callers:
  - services/execution/runtime-manager/paper_fleet_reconciler.py spawns paper_runtime.py per binding
  - services/persona, services/control-plane/governance, services/telemetry, services/trade_journey, BFF, scripts, and reproduce_p3001_issues.py imports
  - services/execution/lean_runtime/test_*.py and tests/e2e/test_lean_*_memory_e2e.py
runtime_or_deploy_refs:
  - docker-compose.yml paper-signal-producer
  - docker-compose.yml paper-fleet-reconciler child worker command
  - docker-compose.exec.yml LEAN images and pantheon-paper-runtime
replacement: null
replacement_proof: Dynamic fleet and current artifact signal contracts exercise these modules.
disposition: retain
validation:
  - paper runtime topology, fleet reconciler, current artifact signal, binding isolation, and LEAN memory E2E suites.
```

This `retain` record covers every non-fixture implementation in the declared
directory: `__init__.py`, the twelve modules named above, `Dockerfile`, and
`requirements.txt`. The directory-local `test_*.py` files remain their focused
validation callers. `lean_smoke_contract.md` and `review_p3001_gemini.md` are
documentation/evidence, not alternate runtime owners.

```yaml
path_or_symbol: docker-compose.yml::pantheon-paper-runtime and docker-compose.exec.yml::pantheon-paper-runtime
behavior: Unbound static paper worker topology using lean_runtime/paper_runtime.py.
callers:
  - scripts/test_paper_runtime_topology_contract.py
  - tests/integration/test_product_functional_compose_contract.py
  - scripts/validate_split_topology.sh
  - scripts/audit_deploy_drift.sh
runtime_or_deploy_refs:
  - Root docker-compose.yml keeps it behind static-paper-runtime and deploy_nonprod_vm.sh rejects/removes it in the normal dynamic-fleet topology.
  - docker-compose.exec.yml still declares it without that profile; validate_split_topology.sh requires and inspects it.
replacement: paper-fleet-reconciler binding-scoped workers in every supported deployment topology
replacement_proof: Present for root Compose, absent for the split EXEC topology.
disposition: defer
validation:
  - Migrate docker-compose.exec.yml and split-topology validation before any service deletion.
  - Keep lean_runtime/paper_runtime.py: it is also the dynamic fleet worker executable.
```

The root static service is not a zero-caller candidate. Removing only its root
profile would leave split-deployment truth divergent; deleting
`paper_runtime.py` would break the authoritative dynamic fleet.

```yaml
path_or_symbol: services/execution/lean_runtime/paper_signal_producer.py::BoundedPaperStrategy and services/execution/lean_runtime/smoke_algorithm.py
behavior: Deterministic bounded paper signals and synthetic LEAN smoke/proof fixtures.
callers:
  - services/trade_journey/hosted_lifecycle_stimulus.py
  - services/persona/agent_usability_validation.py
  - services/execution/lean_runtime/test_algorithm_smoke.py
  - services/execution/lean_runtime/test_paper_signal_producer.py
  - tests/e2e/test_allocation_policy_to_paper_run.py
  - tests/e2e/test_deployment_plan_to_paper_run.py
runtime_or_deploy_refs:
  - docker-compose.yml defaults PAPER_SIGNAL_STRATEGY=artifact; bounded/smoke selection requires an explicit proof/test choice.
replacement: null
replacement_proof: CurrentArtifactStrategy is the product default, while these remain named validation fixtures.
disposition: retain
validation:
  - Assert product default remains artifact and live/canary flags remain false.
  - Run smoke, hosted stimulus, and paper signal strategy tests.
```

### 7. Lifecycle JSON projection and full aggregate rendering

```yaml
path_or_symbol: services/trade_journey/lifecycle_projector.py::LifecycleProjector and BFF JSON trade-journey/loop-run readers
behavior: Reads telemetry incrementally by checkpoint, retains derived aggregate state, and rewrites complete generation JSON payloads for BFF reads.
callers:
  - services/control-plane/bff/main.py
  - services/control-plane/bff/trade_journey_projection_store.py
  - services/control-plane/bff/test_lifecycle_projector_readiness.py
  - services/trade_journey/test_lifecycle_projector.py
  - services/trade_journey/test_lifecycle_projector_compose.py
  - scripts/lifecycle_projector_parity.py
  - docs/operations/lifecycle-projector-incremental-runbook.md
runtime_or_deploy_refs:
  - docker-compose.yml loop-run-projector-scheduler commands lifecycle_projector run
  - LIFECYCLE_PROJECTOR_WRITER_BACKEND defaults disabled, so relational shadow is not active
  - PANTHEON_BFF_TRADE_JOURNEY_READER_BACKEND defaults json
  - BFF event and loop-run paths point to lifecycle-projection/current/*.json
replacement: RelationalLifecycleProjector plus Postgres BFF reader, activated and backfilled by the lifecycle activation/retirement task chain
replacement_proof:
  - Relational implementation and parity harness exist.
  - Current baseline lacks governed backfill/cutover/restart/readback proof; current deployment evidence still reports JSON reader and disabled relational writer.
disposition: defer
validation:
  - PFG-LIFECYCLE-POSTGRES-ACTIVATION-20260824 must prove shadow parity, backfill, cutover, restart, and BFF readback.
  - LIFECYCLE-PROJ-RETIRE-001 owns later JSON retirement; this parent must not race it.
```

The source query itself is bounded (`fetch_after(checkpoint, limit=batch_size)`).
The legacy cost is the retained full derived aggregate and complete JSON
generation rewrite, so deleting a supposed unbounded SQL scan would target the
wrong unit.

### 8. Loop catalog stable spec versus stale runtime claims

```yaml
path_or_symbol: docs/deployment/loop-catalog.registry.json::{loop_id,classification,name,policy_ref,owner,trigger_model,desired_state,actual_state,controller_contract,composed_of}
behavior: Static stable loop identity, specification, ownership, controller, and overlay contract.
callers:
  - services/control-plane/bff/loop_inventory.py::_load_registry,_project_loop,loop_inventory_meta
  - services/control-plane/bff/main.py /bff/v5/loop-inventory and /bff/v5/loop-health surfaces
  - services/control-plane/bff/test_loop_inventory_read_model_contract.py
  - tests/test_loop_catalog_registry.py
runtime_or_deploy_refs:
  - operator-bff reads the repository registry at runtime
replacement: null
replacement_proof: PFG-L12-TRUTH-CROSSLOOP-20260820 retains catalog only as stable spec/owner contract.
disposition: retain
validation:
  - Loop catalog schema/identity tests and BFF loop inventory/health contracts.
```

```yaml
path_or_symbol: docs/deployment/loop-catalog.registry.json::{maturity_levels,truth_levels,loops[].maturity,loops[].evidence_profile,loops[].execution_tasks}
behavior: Static snapshots of runtime maturity, evidence presence, and execution task history.
callers:
  - docs/deployment/loop-catalog.schema.json requires all fields
  - tests/test_loop_catalog_registry.py validates vocabularies, maturity claims, evidence profiles, and task refs
  - services/control-plane/bff/test_loop_inventory_read_model_contract.py mutates them to prove they cannot create liveness
runtime_or_deploy_refs:
  - services/control-plane/bff/loop_inventory.py intentionally does not project these fields and derives runtime maturity only from accepted current health records
replacement: Current loop-controller/health observations exposed by BFF /bff/v5/loop-health
replacement_proof:
  - PFG-L12-TRUTH-CROSSLOOP-20260820 is done at merge 4caa25e509831171f727b2edcdea4677566d8236 and proves static catalog claims do not create liveness.
  - BFF contract tests assert these fields are absent from loop-inventory responses.
disposition: replace_then_delete
validation:
  - Revise loop-catalog.schema.json and tests/test_loop_catalog_registry.py in the same parent change.
  - Preserve stable spec/owner fields and re-run BFF loop inventory/health contracts.
  - Confirm no documentation generator consumes the removed keys; no generator was found on this baseline.
```

The static fields are not yet `delete`: they still have schema and test callers.
The parent can remove them only as a composed registry/schema/test migration;
the BFF already supplies the replacement runtime truth.

## Parent handoff order and stop conditions

1. Delete only `services/source_ingestion/scheduler_worker.py` and its dedicated
   test after repeating the negative caller scan. Preserve the route and manual
   CLI used by `controller_worker`.
2. Remove automatic Agora discovery only after the parent-level Agora journey
   proves the durable handoff path, then update the no-ref route/scheduler tests
   as one change. Do not delete exact-ref authority or candidate handoff code.
3. Treat the root static paper profile as already non-authoritative, but stop
   deletion while split EXEC Compose still declares it. This is concrete
   deployment ownership, not a historical mention.
4. Do not modify lifecycle projection in this parent. The current deployed
   defaults are JSON and the corrected lifecycle activation/retirement chain
   owns its cutover.
5. Remove loop static runtime/task fields only with their schema and tests;
   retain the catalog's stable spec/owner/controller fields.

Any new active import, route client, workflow, Compose service, deploy-script
reference, generated consumer, or current operational runbook found during the
parent implementation invalidates the corresponding deletion disposition and
must move it back to `replace_then_delete` or `defer`.

## Reproducible scan and focused validation

The inventory used `rg -n`/`rg -l` across product Python, route strings,
`.github/workflows`, `docker-compose*.yml`, `scripts/`, tests, and current
operations/design docs for these anchors:

```text
services.source_ingestion.scheduler_worker | source_ingestion/scheduler_worker.py
source_ingest_scheduler_once | source-ingest-scheduler | source-ingest-agora-projector
agora_dataset_authority | discover_eligible_datasets | list_dataset_versions
shadow-eval-tick | agora-handoff | candidate_experiment_handoff
services.execution.lean_runtime | pantheon-paper-runtime | static-paper-runtime
BoundedPaperStrategy | smoke_algorithm
LIFECYCLE_PROJECTOR_WRITER_BACKEND | PANTHEON_BFF_TRADE_JOURNEY_READER_BACKEND
lifecycle-projection/current | loop-catalog.registry.json
maturity | evidence_profile | execution_tasks
```

Generated archives, `current-work.md`, `ai-status.json`, and the activity log
were excluded from caller counts.

Focused validation on the audited baseline:

- 64 tests passed across the current Agora handoff cutover, Source manual
  one-shot and Compose activation, product functional Compose, paper topology,
  loop catalog schema, and BFF loop inventory/read-model suites.
- Adding `services/trade_journey/test_lifecycle_projector_compose.py` produced
  70 passed and one pre-existing failure:
  `test_bff_only_deploy_rebuilds_its_lifecycle_projector_only` expects an old
  one-line `docker compose ... up` string, while the current deploy script uses
  separate candidate build/recreate phases. `HEAD` and `origin/dev` were both
  `8ca38337dee63d8759967ff5d670eaf24b4f983b`, with no diff in either lifecycle
  file. This task did not repair that out-of-scope deploy contract; it is
  additional evidence for `defer`.

The parent must repeat affected suites after making implementation changes.
