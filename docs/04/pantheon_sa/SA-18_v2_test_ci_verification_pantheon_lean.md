---
project: Pantheon
document_type: System Analysis Gap Report
revision: v2-corrected
language: zh-TW
updated_reason: >
  依據最新 Codex 盤點校正：目前 Pantheon 實際接入的是 `pantheon/lean` submodule，
  remote 為 `ajoe734/pantheon-lean.git`，且已含 PantheonAlgoBase / Pantheon LEAN bridge。
  `lean-platform` 雖已 clone，但不是目前 Pantheon 實際接的 Lean repo，且未命中 Pantheon / RuntimeBinding / SignalStore 等整合訊號。
baseline_note: >
  本批修正版不再將 `Lean` 與 `lean-platform` 粗略二分，而是明確區分：
  1) `pantheon/lean` submodule / `ajoe734/pantheon-lean.git` = 目前實際 bridge；
  2) `ajoe734/lean-platform` = 未對齊 / 歷史或遷移候選；
  3) generic upstream Lean = LEAN engine 基底概念。
---

> **2026-05-03 Canonical correction**: `pantheon/lean` submodule backed by `ajoe734/pantheon-lean.git` is the official execution substrate. Any older `lean-platform` repo-mapping drift language in this SA note is superseded; do not treat `lean-platform` as an active gap or task target.





# SA-18 v2 — Test / CI / Verification 差異分析：加入 `pantheon/lean` Submodule Baseline

## 1. 本章修正重點

本章取代原 SA-18。
原版 SA-18 已指出需要 cross-repo behavioral verification。
根據最新 Codex 盤點，測試與 CI 必須重新聚焦於：

```text
pantheon/lean submodule
remote = ajoe734/pantheon-lean.git
PantheonAlgoBase bridge
runtime_bootstrap.py
docker-compose.exec.yml /workspace/lean/Launcher/config.json
```

而不是泛稱 `Lean` 或錯誤指向 `lean-platform`。

---

## 2. 新的 CI 核心假設

```text
official_current_bridge_path = pantheon/lean
official_current_bridge_remote = ajoe734/pantheon-lean.git
inactive_repo = ajoe734/lean-platform
runtime_bootstrap = services/execution/lean_runtime/runtime_bootstrap.py
paper_runtime = truthful baseline
live_runtime = health-only placeholder unless activated
```

---

## 3. 需要新增的 CI 類別

### 3.1 Submodule authority tests

```text
test_gitmodules_has_lean_submodule
test_lean_submodule_remote_is_pantheon_lean
test_lean_submodule_contains_pantheon_algo_base
test_exec_compose_mounts_workspace_lean
test_no_p0_execution_task_targets_lean_platform
```

### 3.2 Runtime bootstrap tests

```text
test_runtime_bootstrap_paper_role_starts_paper_runtime
test_runtime_bootstrap_live_role_is_health_only_by_default
test_live_placeholder_fails_closed_for_broker_actions
test_runtime_bootstrap_reports_healthz_readyz_livez_consistently
```

### 3.3 Bridge tests

```text
test_pantheon_algo_base_importable
test_pantheon_algo_base_accepts_signal_context
test_pantheon_algo_base_emits_paper_runtime_event
test_pantheon_algo_base_does_not_require_live_broker_for_paper
```

### 3.4 Compose contract tests

```text
test_default_compose_dev_single_vm
test_control_compose_services_use_readyz_or_known_legacy_health
test_exec_compose_points_to_workspace_lean_launcher_config
test_source_search_default_jsonl_in_dev
test_staging_posture_requires_postgres_object_store
```

### 3.5 Health endpoint cleanup tests

```text
test_default_compose_uses_readyz
test_control_exec_compose_no_legacy_health_after_cleanup
test_metrics_endpoint_available_for_services
```

---

## 4. Updated cross-repo e2e test plan

### 4.1 Current realistic paper baseline test

不要一開始測 full live。現在應先測：

```text
1. pantheon starts dev compose.
2. execution service starts runtime_bootstrap.py in paper role.
3. pantheon/lean submodule is mounted at /workspace/lean.
4. PantheonAlgoBase is importable / referenced.
5. paper runtime emits health / basic event.
6. telemetry ingest accepts paper heartbeat.
7. BFF reports paper runtime status.
```

### 4.2 Not yet expected

以下不應列為 current pass criteria：

```text
full Lean Launcher production live
broker SDK live order execution
canary account binding
live bracket order placement
HA BFF 2 replicas
OpenClaw live broker adapter
Qlib / FinRL production activation
```

這些應標為 deferred / fail-closed / not activated。

---

## 5. Test matrix v2

| Test Area | Old Target | Revised Target | Reason |
|---|---|---|---|
| execution repo | Lean / lean-platform | pantheon/lean submodule | actual bridge |
| bridge import | unknown | pantheon_algo/base.py | PantheonAlgoBase exists |
| compose runtime | generic | docker-compose.exec.yml /workspace/lean | actual path |
| paper runtime | unknown | runtime_bootstrap.py paper role | truthful baseline |
| live runtime | expected? | health-only placeholder | intentional safety |
| bracket order | execution | log-only expected until activation | current state |
| source/search | missing? | bounded connector/indexer baseline | Codex says present |
| research OSS | production? | explicit data/model posture before promotion; no direct order routing | intentional boundary |
| OpenClaw | runtime? | facade / env-gated | correct boundary |
| BFF HA | production? | deferred | intentional |
| frontend auth | production? | demo/local token gap | actual production gap |

---

## 6. Revised Minimum Operating Loop Test

### 6.1 Current P0 test

```text
StrategySpec / CandidateArtifact can be mocked or seed-backed for this test.
ApprovalDecision / DeploymentPlan can be minimal but must be canonical.
RuntimeBinding must be created.
runtime_bootstrap paper role must start.
pantheon/lean submodule must be mounted.
paper runtime heartbeat must reach telemetry ingest.
BFF must show runtime status from non-mock source.
```

### 6.2 Required trace IDs

```text
deployment_plan_id
runtime_binding_id
runtime_id
artifact_id
deployment_stage=paper
engine_bridge_repo=pantheon-lean
engine_bridge_commit
```

### 6.3 Acceptance

```text
No live broker action occurs.
No lean-platform code is used.
No preview mock is used.
Telemetry includes paper runtime identity.
```

---

## 7. Test classification

### 7.1 Actual gaps

```text
DeploymentPlan → runtime_bootstrap contract test missing
RuntimeBinding → paper runtime context test missing
TelemetryEvent exporter / heartbeat test missing
frontend source_mode tests missing
health endpoint standardization tests missing
```

### 7.2 Intentional deferrals

```text
BFF HA/LB production topology
full production Lean live kernel
OpenClaw live broker
Qlib/TRL/FinRL/RLlib production activation
full Postgres-only default dev
```

### 7.3 Safety gates

```text
live placeholder health-only
research / policy learning fail-closed
OpenClaw adapters off by env gate
guarded external_feed rather than unrestricted crawler
```

---

## 8. CI jobs v2

| CI Job | Purpose |
|---|---|
| ci-submodule-authority | Assert pantheon/lean remote / path / bridge files |
| ci-compose-dev-baseline | Validate default compose dev single-VM |
| ci-compose-staging-split | Validate control/exec compose split |
| ci-runtime-bootstrap-paper | Run paper bootstrap smoke |
| ci-runtime-live-placeholder | Assert live role cannot trade by default |
| ci-telemetry-paper-heartbeat | Validate paper heartbeat event path |
| ci-source-search-bounded | Validate configured connector / DLQ / incremental refresh smoke |
| ci-research-fail-closed | Validate Qlib/TRL/FinRL/RLlib adapters fail closed |
| ci-openclaw-facade | Validate broker/live/capital binding disabled by default |
| ci-front-no-demo-prod | Detect `@/demo` imports in production routes |
| ci-health-endpoints | Detect legacy `__health__` in staging compose |
| ci-no-lean-platform-target | Fail P0 execution tasks targeting lean-platform |

---

## 9. Frontend verification updates

Codex 指出：

```text
BFF client 已集中接 BFF。
App route 已掛 operator / research / knowledge / consultation / governance。
但 dashboard / persona tabs / health / evolution / tools / settings security / trainer 舊頁仍有 @/demo imports。
```

因此前端 CI 要加：

```text
test_no_demo_imports_in_production_routes
test_auth_provider_not_demo_for_staging_prod
test_login_no_demo_copy_for_staging_prod
test_bff_routes_have_source_mode
test_preview_mock_banner_only_in_lovable_preview
```

---

## 10. Auth verification updates

Codex 指出：

```text
BFF 有 HS256 JWT，optional JWKS/OIDC。
前端 AuthProvider 仍 import @/demo/api，寫 demo token 到 pantheon_operator_token。
```

新增測試：

```text
test_bff_accepts_valid_jwt
test_bff_rejects_invalid_jwt
test_front_staging_does_not_use_demo_auth
test_front_prod_requires_oidc_or_enterprise_auth
```

---

## 11. Source/Search verification updates

Codex 指出 source/search 已經是 bounded autonomous baseline：

```text
source-ingest supports configured connector / scheduler / DLQ / frontier / audit replay
fetch mode supports static_records and guarded external_feed
search has durable repo and incremental refresh pipeline
```

因此測試不應寫成「完全缺 source/search」，而應改成：

```text
test_static_records_connector
test_guarded_external_feed_connector
test_source_ingest_dlq
test_source_frontier_scheduler
test_audit_replay
test_search_incremental_refresh
test_search_durable_repo
test_no_unrestricted_crawler_by_default
```

---

## 12. Research / OSS verification updates

Codex 指出 OSS/research/learning 需要的是 production data/model posture，而不是 blanket live-data ban：

```text
Qlib / TRL / FinRL / RLlib / Ray / W&B production data/model paths require explicit posture/promotion evidence and cannot route directly to orders; OpenClaw broker/live/capital binding remains off by default.
```

新增測試：

```text
test_qlib_activation_requires_data_posture_and_promotion_evidence
test_finrl_activation_requires_data_posture_and_promotion_evidence
test_rllib_activation_requires_data_posture_and_promotion_evidence
test_openclaw_broker_adapter_disabled_by_default
test_offline_smoke_allowed_when_flag_enabled
```

---

## 13. Revised Definition of Done

目前階段的 DoD 不應要求 live production 完成，而應要求：

```text
1. pantheon/lean submodule authority confirmed.
2. lean-platform not targeted by P0 runtime tasks.
3. runtime_bootstrap paper role passes smoke test.
4. live role fail-closed health-only by default.
5. paper runtime emits telemetry heartbeat.
6. BFF runtime status reads non-mock data.
7. source/search bounded baseline tests pass.
8. research/learning production posture and no-direct-order-routing tests pass.
9. front production routes do not depend on demo islands.
10. health endpoint cleanup either completed or tracked as explicit remaining cleanup.
```

---

## 14. 本章結論

SA-18 v2 的核心結論：

> **測試與 CI 現在不能再泛泛驗證 Lean / lean-platform，而必須精準驗證 `pantheon/lean` submodule / `pantheon-lean.git` 這條 actual bridge。當前應以 paper runtime baseline 為最小閉環測試對象，並將 live production kernel、BFF HA、OSS production activation 標為 intentional deferral 或後續 activation，而不是錯誤地當成當前必須通過的測試。**
