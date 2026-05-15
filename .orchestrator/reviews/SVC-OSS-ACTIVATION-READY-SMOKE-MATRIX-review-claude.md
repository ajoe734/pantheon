# Review: SVC-OSS-ACTIVATION-READY-SMOKE-MATRIX

Reviewer: Claude
Date: 2026-04-30
Decision: **approve**

---

## Scope Verified

Task: Add activation-ready OSS smoke matrix
Owner: Codex2
Artifacts reviewed:
- `scripts/smoke_oss_activation_ready_matrix.py` (new)
- `scripts/test_smoke_oss_activation_ready_matrix.py` (new)
- `docker-compose.yml` (modified — `oss-activation-ready-smoke-matrix` service added)
- `services/research-worker-gateway/main.py` (gate and rejection logic)
- `services/research/qlib/worker.py`, `services/learning/trl/worker.py` (activation-ready workers)
- `services/research/finrl/worker.py`, `services/research/rllib/worker.py` (deferred-prep workers)
- `services/registry/experiments/adapter.py` (W&B offline local backend)

---

## Acceptance Criteria Assessment

| Criterion | Evidence | Verdict |
|---|---|---|
| default matrix proves qlib/trl/rl/wandb and paper/canary/live fail closed | `_default_fail_closed_rows()` loads gateway with `PANTHEON_OFFLINE_GATE_ENABLED=false`; qlib/trl/finrl/rllib/wandb dispatched with `dispatch_mode=offline` → `dispatch_mode_disabled`; paper/canary/live dispatched as `requested_mode` → `production_adapter_disabled` regardless of gate state | ✅ pass |
| enabled offline matrix runs bounded jobs and emits artifacts | `_enabled_gateway_rows()` loads gateway with `PANTHEON_OFFLINE_GATE_ENABLED=true`; qlib/trl/finrl/rllib dispatched with correct activation env vars; workers execute as subprocess with output dir set inside temp; `artifact_state=draft`, `deployment_stage=none`, and path containment all verified | ✅ pass |
| no registry/governance/broker/live writes occur | All `writes` dicts are `_empty_writes()` (all False); `_assert_no_forbidden_writes()` also verifies artifact paths cannot escape the temp dir; gateway code explicitly rejects `registry_write`, `governance_write`, `lean`/`signalstore`/`execution-plane` tokens in any request | ✅ pass |
| compose profiles document activation-ready but disabled posture | `oss-activation-ready-smoke-matrix` service uses `profiles: ["activation-ready-smoke"]`; `PANTHEON_OFFLINE_GATE_ENABLED=false` and `RESEARCH_WORKER_GATEWAY_ENABLE_PRODUCTION_ADAPTERS=false` set by default; no ports exposed; verified by `test_compose_documents_activation_ready_smoke_profile` | ✅ pass |
| CI-friendly focused tests pass | Three focused tests: full matrix run (12 rows), CLI JSON report writing, and compose profile structure check; all run without external dependencies | ✅ pass |

---

## Additional Observations

**Gate isolation:** `_load_gateway_module()` uses unique module names per `(offline_gate, data_dir)` pair and applies `mock.patch.dict("os.environ", ...)` during load so module-level constants (`OFFLINE_GATE_ENABLED`, `DATA_DIR`, etc.) are correctly set for each variant without cross-contamination.

**Backend env var alignment:** Confirmed env var names match adapter/gate expectations exactly: `PANTHEON_FINRL_PREP_ENABLED` → `DeferredPrepGate.require_env()`, `PANTHEON_RLLIB_PREP_ENABLED` → RLlib gate, `PANTHEON_FINRL_BACKEND` → `finrl/config.py selected_backend()`, `PANTHEON_RLLIB_BACKEND` → `rllib/config.py selected_backend()`.

**W&B row correctness:** `_wandb_offline_row()` directly exercises `OfflineWandbLocalBackend` + `RegistryExperimentAdapter.sync_registry_entry()` with `artifact_state=candidate`/`deployment_stage=none`; result `sync_status=offline_local`, `online_sync.enabled=False`, and local artifact file existence are all verified. Online sync gate remains closed.

**Artifact path handling:** `_artifact_paths()` correctly handles both output formats — `artifact_manifest.files` (qlib/trl) and `artifact_paths` dict (finrl/rllib) — in a single pass.

**Paper/canary/live always blocked:** The `requested_mode in PRODUCTION_MODES` check in the gateway fires before the offline gate check, so paper/canary/live are blocked even when `PANTHEON_OFFLINE_GATE_ENABLED=true`. The enabled matrix verifies this via three additional rows using rllib + production modes.

No issues found. All acceptance criteria are satisfied and the boundary invariants are well-enforced.
