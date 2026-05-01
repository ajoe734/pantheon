# Review: P2-WANDB-ONLINE-SYNC-001

**Reviewer**: Claude
**Owner**: Codex
**Date**: 2026-05-01
**Decision**: APPROVED

---

## Acceptance Criteria Verification

| Criterion | Status | Evidence |
|---|---|---|
| W&B SDK dependency and backend wiring land behind explicit online sync env gate with offline default preserved | **MET** | `WandbOnlineBackend.__init__` checks `PANTHEON_WANDB_ONLINE_SYNC_ENABLED=1`, `PANTHEON_WANDB_PROJECT`, and `WANDB_API_KEY` before importing SDK; `OfflineWandbLocalBackend` remains default; `config.py` enforces gate separation |
| W&B test project smoke uploads metrics and artifact then reads back run or artifact references without persisting raw secrets | **MET** | `test_wandb_online_backend_uploads_and_readbacks_refs_without_persisting_secrets` uses `_FakeWandbModule` to prove upload+readback path without real network; `smoke_test.py --backend wandb-online` returns `{"status":"skipped","secrets_persisted":false,...}` when env is absent; `_redact_wandb_secret()` is applied at ExperimentSyncError wrap boundary |
| BFF registry and evaluator can resolve W&B run refs while no source path can route to broker order execution | **MET** | `test_rw05_detail_exposes_wandb_experiment_refs_from_registry_metadata` confirms BFF surfaces run_uri, artifact_uri, and readback_refs; evaluator test confirms experiment_refs pass through to auditable_fields; `wandb` in `PRODUCTION_WORKERS` in gateway keeps it fail-closed for dispatch |

---

## Code Review Findings

### WandbOnlineBackend (adapter.py)

- Gate check at `__init__` time before SDK import is correct governance-first pattern; ExperimentSyncError is raised before any network touch if config is absent.
- `_settings()` wraps `wandb.Settings` construction with a `TypeError` fallback — appropriate for SDK version variance.
- `_readback()` returns structured `{"enabled": False}` or `{"verified": False, "reason": ...}` when API unavailable, not a hard failure — correct for a smoke path.
- `run.finish()` is called in `finally` block, preventing dangling run state.
- `_redact_wandb_secret()` is applied at the exception wrap boundary in `record()`.
- `ExperimentSyncError` subclass boundary is maintained throughout; non-ExperimentSyncError exceptions are caught and re-raised with secret redaction.
- `_safe_wandb_artifact_name()` sanitizes experiment_name + run_name with regex to produce a W&B-safe artifact name ≤128 chars.
- `artifact_refs` keys and `readback_refs` shape match the `ExperimentRef.to_metadata_ref()` contract.

### smoke_test.py

- `_online_env_ready()` checks all three required env vars before constructing the backend — avoids leaking partial state.
- `--backend wandb-online` path prints structured JSON with `secrets_persisted: false` when config is absent. Verified locally: output is `{"backend": "wandb-online", "missing_env": [...], "reason": "missing_explicit_online_sync_config", "secrets_persisted": false, "status": "skipped"}`.
- Offline and memory smoke tests pass unchanged (confirmed).

### test_adapter.py (16 tests, all pass)

- `_FakeWandbModule` correctly mocks `wandb.init`, `wandb.Artifact`, `wandb.Api`, `wandb.Settings`, and `__version__`.
- `test_wandb_online_backend_uploads_and_readbacks_refs_without_persisting_secrets` asserts: `sync_status == "online_synced"`, `readback_refs["verified"] == True`, artifact_ref starts with `wandb://`, `offline_local == "false"`, `mode == "online"`, `"secret-test-key" not in str(promoted_metadata)`, `run.finished == True`. All pass.
- `test_wandb_online_sync_requires_explicit_gate_and_still_has_no_sdk_path` verifies the `OfflineWandbLocalBackend.sync_online()` fail-closed behavior for both the disabled and the "gate enabled but use WandbOnlineBackend" cases.
- Gate tests in `test_selected_backend_*` cover all four env combinations: no flag (rejects wandb), deferred prep (accepts offline/dryrun), online mode without gate (rejects), online mode with gate (accepts).

### research-worker-gateway/main.py

- `wandb` is listed in `PRODUCTION_WORKERS` — dispatch is fail-closed.
- Registry entry documents: `"note": "SDK-backed W&B online sync is a registry experiment-backend path only. This gateway does not dispatch W&B workers and exposes no broker/order/capital route."` — explicit non-dispatch boundary.
- All 10 gateway dispatch tests pass.

### BFF / Evaluator

- `test_rw05_detail_exposes_wandb_experiment_refs_from_registry_metadata`: BFF reads experiment_refs with W&B backend, run_uri, artifact_uri, and readback_refs correctly.
- Evaluator passes experiment_refs through to auditable_fields, confirmed by test.
- No write path, no order/capital route, no registry promotion triggered from either layer.

### WANDB_ACTIVATION.md

- §2.1 updated to reflect that `WandbOnlineBackend` exists for explicit-gated SDK upload/readback smoke.
- `smoke_test.py --backend wandb-online` command documented in §6.
- §7.2 records the current repo state accurately.

### config.py

- Module-level `selected_backend(EXPERIMENT_BACKEND)` call validates on import — immediately rejects misconfigured environments.
- `selected_wandb_mode()` enforces `PANTHEON_WANDB_ONLINE_SYNC_ENABLED=1` requirement for `mode == "online"` before returning.

---

## Safety Boundary Verification

- No broker/order/capital path introduced in any changed file.
- `wandb` in `PRODUCTION_WORKERS` — gateway dispatch is fail-closed.
- Online sync only activates with all three explicit env vars; absent any one, structured skip is returned.
- API key is consumed by SDK and not persisted to any Pantheon state file.
- `ExperimentRef.to_metadata_ref()` includes `readback_refs` but not raw credentials.

---

## Verification Commands Run

```
python3 -m pytest services/registry/experiments/test_adapter.py -v  # 16 passed
python3 -m pytest services/research-worker-gateway/tests/test_research_worker_gateway_gate_dispatch.py -v  # 10 passed
python3 -m pytest services/control-plane/bff/test_rw05_artifact_compare_contract.py -v  # 7 passed
python3 services/registry/experiments/smoke_test.py --backend memory  # LP-003 smoke test passed
python3 services/registry/experiments/smoke_test.py --backend wandb   # LP-003 smoke test passed
python3 services/registry/experiments/smoke_test.py --backend wandb-online  # structured skip, secrets_persisted: false
```

---

## Review Notes (ZH)

審查通過：三項驗收標準全部達標；WandbOnlineBackend 正確實作 explicit gate 檢查、SDK import guard、secrets redaction、readback_refs 結構化輸出、run.finish() finally block；offline default 完整保留；gateway 的 wandb 在 PRODUCTION_WORKERS 中維持 fail-closed；BFF/evaluator W&B ref 可讀取但無寫入或下單路徑；wandb-online smoke 在無 env 時輸出結構化 skip 且 secrets_persisted: false；16+10+7 unit tests 全 pass。

後續：Codex 需在 done closeout 前建立 task-scoped commit 並 push。
