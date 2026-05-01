---
project: Pantheon
document_type: P0 System Design / Architecture Decision / Codex Implementation Packet
language: zh-TW
status: draft-for-implementation
revision: v1
baseline: >
  Based on Pantheon consolidated blueprint and latest implementation correction:
  current actual LEAN bridge is `pantheon/lean` submodule, remote `ajoe734/pantheon-lean.git`;
  `lean-platform` is not the current Pantheon execution target.
---

# SD-P0-06 — Submodule / Compose / Health CI Verification

## 1. Purpose

本 SD 定義 P0 CI / verification 工作，確保目前 Pantheon 的 execution bridge、compose topology、health endpoint、source/search、fail-closed adapters 不再被誤判或漂移。

此文件是把 SA-18 v2 轉成可施工 CI 規格。

---

## 2. Verification Goals

```text
1. Verify official current bridge is pantheon/lean submodule.
2. Verify bridge remote is ajoe734/pantheon-lean.git.
3. Verify pantheon/lean includes PantheonAlgoBase.
4. Verify docker-compose.exec.yml points to /workspace/lean/Launcher/config.json.
5. Verify P0 execution work does not target lean-platform.
6. Verify runtime_bootstrap paper baseline works.
7. Verify live role is health-only fail-closed.
8. Verify health endpoints are standardized or tracked.
9. Verify source/search bounded baseline remains guarded.
10. Verify research/learning/OpenClaw production adapters remain fail-closed by default.
```

---

## 3. Target Repos / Paths

### 3.1 pantheon

```text
.gitmodules
docker-compose.yml
docker-compose.control.yml
docker-compose.exec.yml
services/execution/lean_runtime/runtime_bootstrap.py
services/source-ingest/
services/search/
services/research/
services/policy-learning/
integrations/openclaw/
scripts/
.github/workflows/
```

### 3.2 submodule

```text
pantheon/lean/
pantheon/lean/pantheon_algo/base.py
```

### 3.3 frontend

Frontend verification handled mostly in SD-P0-05 but CI may include cross-repo checks.

---

## 4. CI Job: Submodule Authority

### 4.1 Checks

```text
SUBMOD-001:
  .gitmodules contains path = lean

SUBMOD-002:
  submodule remote equals ajoe734/pantheon-lean.git

SUBMOD-003:
  pantheon/lean/pantheon_algo/base.py exists

SUBMOD-004:
  pantheon/lean HEAD can be printed as bridge commit

SUBMOD-005:
  docker-compose.exec.yml references /workspace/lean/Launcher/config.json
```

### 4.2 Failure behavior

```text
Fail CI.
Do not run execution smoke.
Do not allow runtime deployment.
```

### 4.3 Suggested script

```text
scripts/check_execution_bridge.py
```

Output:

```json
{
  "bridge_path": "pantheon/lean",
  "bridge_remote": "ajoe734/pantheon-lean.git",
  "bridge_commit": "...",
  "pantheon_algo_base_present": true,
  "compose_exec_path_valid": true
}
```

---

## 5. CI Job: No Wrong Repo Target

### 5.1 Checks

Scan Codex / orchestrator / task packets for P0 execution target:

```text
lean-platform
ajoe734/lean-platform
```

If found, require:

```yaml
migration_only: true
adr_override: ADR-EXEC-001-revision
```

### 5.2 Failure behavior

```text
Fail CI for P0 execution tasks targeting lean-platform without migration_only.
```

---

## 6. CI Job: Runtime Bootstrap Paper

### 6.1 Checks

```text
BOOT-001:
  runtime_bootstrap.py can start in paper role.

BOOT-002:
  paper role does not require live broker secret.

BOOT-003:
  paper role reports health.

BOOT-004:
  paper role can emit or schedule heartbeat event.

BOOT-005:
  paper role includes bridge identity where available.
```

### 6.2 Non-requirements

```text
No live broker connection.
No canary account.
No real order.
No bracket broker submission.
```

---

## 7. CI Job: Live Role Fail-Closed

### 7.1 Checks

```text
LIVE-001:
  runtime_bootstrap live role starts health-only sidecar by default.

LIVE-002:
  live role cannot place broker order.

LIVE-003:
  live role reports not_activated / health_only.

LIVE-004:
  live role has no broker secret requirement.

LIVE-005:
  bracket order remains logged_only in current mode.
```

### 7.2 Failure behavior

Any live broker action without activation flag:

```text
Critical CI failure.
```

---

## 8. CI Job: Compose Contract

### 8.1 default compose

Current expected:

```text
dev single-VM baseline
source/search may use JSON/JSONL fallback
mostly /readyz
```

Checks:

```text
COMPOSE-DEV-001:
  default compose is labeled dev baseline.

COMPOSE-DEV-002:
  source/search dev fallback is explicit.

COMPOSE-DEV-003:
  runtime services do not enable live broker by default.
```

### 8.2 control / exec compose

Expected:

```text
staging control VM and execution VM split exists
not equal to production HA
```

Checks:

```text
COMPOSE-STG-001:
  docker-compose.control.yml exists.

COMPOSE-STG-002:
  docker-compose.exec.yml exists.

COMPOSE-STG-003:
  exec compose references /workspace/lean.

COMPOSE-STG-004:
  any legacy __health__ endpoints are reported.
```

---

## 9. CI Job: Health Endpoint Cleanup

### 9.1 Current issue

Default compose mostly uses:

```text
/readyz
```

But control / exec compose still contains:

```text
__health__
```

### 9.2 Checks

```text
HEALTH-001:
  /healthz, /livez, /readyz, /metrics helper exists.

HEALTH-002:
  default compose uses /readyz where expected.

HEALTH-003:
  control / exec compose legacy __health__ occurrences are flagged.

HEALTH-004:
  after cleanup, __health__ is forbidden.
```

### 9.3 Staged mode

Before cleanup complete:

```text
warn mode
```

After cleanup PR:

```text
fail mode
```

---

## 10. CI Job: Source/Search Bounded Baseline

Codex reported source/search already supports:

```text
configured connector
scheduler
DLQ
frontier
audit replay
static_records
guarded external_feed
durable repo
incremental refresh
```

### 10.1 Checks

```text
SRC-001:
  static_records connector smoke test.

SRC-002:
  guarded_external_feed requires config guard.

SRC-003:
  DLQ path exists.

SRC-004:
  frontier scheduler smoke.

SRC-005:
  audit replay smoke.

SRC-006:
  search incremental refresh smoke.

SRC-007:
  no unrestricted crawler enabled by default.
```

---

## 11. CI Job: Research / Learning Fail-Closed

Codex reported:

```text
Qlib / TRL / FinRL / RLlib / Ray / W&B production adapters fail-closed.
```

### 11.1 Checks

```text
OSS-001:
  qlib production activation disabled by default.

OSS-002:
  finrl production activation disabled by default.

OSS-003:
  rllib/ray production activation disabled by default.

OSS-004:
  offline smoke may run only under explicit flag.

OSS-005:
  activation attempt without flag returns fail-closed status.
```

---

## 12. CI Job: OpenClaw Facade Fail-Closed

Codex reported:

```text
OpenClaw adapter exists.
production broker / paper adapter / live adapter / capital binding default off.
```

### 12.1 Checks

```text
OC-001:
  OpenClaw broker adapter disabled by default.

OC-002:
  OpenClaw live adapter disabled by default.

OC-003:
  OpenClaw capital binding disabled by default.

OC-004:
  OpenClaw cannot access broker secret.

OC-005:
  OpenClaw cannot call runtime directly.
```

---

## 13. CI Job: Frontend Demo Production Guard

See SD-P0-05. Include minimal cross-check:

```text
FRONT-001:
  staging/prod route set has no @/demo imports.

FRONT-002:
  AuthProvider staging/prod has no demo token write.

FRONT-003:
  Login staging/prod has no demo copy.

FRONT-004:
  preview mock fallback only on preview host.
```

---

## 14. CI Matrix

| Job | Repo | Mode | P0 |
|---|---|---|---|
| ci-submodule-authority | pantheon | fail | yes |
| ci-no-lean-platform-target | pantheon | fail | yes |
| ci-runtime-bootstrap-paper | pantheon | fail | yes |
| ci-live-fail-closed | pantheon | fail | yes |
| ci-compose-contract | pantheon | warn/fail | yes |
| ci-health-endpoint-cleanup | pantheon | warn then fail | yes |
| ci-source-search-bounded | pantheon | fail | yes |
| ci-research-fail-closed | pantheon | fail | yes |
| ci-openclaw-facade | pantheon | fail | yes |
| ci-front-demo-prod-guard | front | fail | yes |

---

## 15. Hard Invariants

```text
INV-CI-001:
  CI must know the official current bridge path.

INV-CI-002:
  CI must fail if P0 execution work targets lean-platform without migration override.

INV-CI-003:
  live role must fail closed by default.

INV-CI-004:
  paper smoke must not require broker secret.

INV-CI-005:
  source/search must not enable unrestricted crawler by default.

INV-CI-006:
  research/learning production adapters must fail closed by default.

INV-CI-007:
  OpenClaw must not enable broker/live/capital binding by default.

INV-CI-008:
  staging/prod frontend must not use demo auth.

INV-CI-009:
  health endpoint cleanup must be tracked to closure.

INV-CI-010:
  CI results must distinguish actual gaps from intentional deferrals.
```

---

## 16. Non-goals

```text
1. Do not make CI require full live broker runtime.
2. Do not remove dev JSON/JSONL fallback.
3. Do not enable unrestricted crawler.
4. Do not enable research OSS production adapters.
5. Do not enable OpenClaw broker/live adapter.
6. Do not implement BFF HA/LB.
7. Do not migrate to lean-platform.
```

---

## 17. Acceptance Criteria

```text
AC-CI-001:
  Submodule authority CI passes.

AC-CI-002:
  Runtime bootstrap paper smoke passes.

AC-CI-003:
  live role fail-closed test passes.

AC-CI-004:
  no P0 execution task targets lean-platform.

AC-CI-005:
  source/search bounded tests pass.

AC-CI-006:
  research/learning fail-closed tests pass.

AC-CI-007:
  OpenClaw facade fail-closed tests pass.

AC-CI-008:
  frontend demo production guard exists.

AC-CI-009:
  health endpoint legacy occurrences are reported or cleaned.

AC-CI-010:
  CI report includes official bridge path and commit.
```

---

## 18. Codex Task Packets

### TP-CI-001 — Submodule authority script

```yaml
task_id: TP-CI-001
repo: pantheon
goal: Add script to verify pantheon/lean submodule authority.
target_paths:
  - scripts/check_execution_bridge.py
  - .github/workflows/*
acceptance:
  - verifies remote
  - verifies PantheonAlgoBase
  - verifies compose exec path
```

### TP-CI-002 — No lean-platform P0 target guard

```yaml
task_id: TP-CI-002
repo: pantheon
goal: Fail P0 execution tasks targeting lean-platform without migration override.
target_paths:
  - scripts/check_task_targets.py
  - docs/codex/*
  - .orchestrator/*
acceptance:
  - migration_only override supported
  - default failure on lean-platform target
```

### TP-CI-003 — Runtime bootstrap paper / live tests

```yaml
task_id: TP-CI-003
repo: pantheon
goal: Add paper bootstrap and live fail-closed tests.
target_paths:
  - services/execution/lean_runtime/tests/*
acceptance:
  - paper role starts
  - live role health-only
  - no broker secret required
```

### TP-CI-004 — Health endpoint cleanup scan

```yaml
task_id: TP-CI-004
repo: pantheon
goal: Scan compose files for legacy __health__ and report/cleanup.
target_paths:
  - docker-compose.control.yml
  - docker-compose.exec.yml
  - scripts/check_health_endpoints.py
acceptance:
  - lists legacy occurrences
  - cleanup mode can fail CI
```

### TP-CI-005 — Source/search bounded tests

```yaml
task_id: TP-CI-005
repo: pantheon
goal: Add smoke tests for source/search bounded baseline.
target_paths:
  - services/source-ingest/tests/*
  - services/search/tests/*
acceptance:
  - static_records smoke
  - guarded external_feed smoke
  - DLQ / frontier / incremental refresh smoke
```

### TP-CI-006 — OpenClaw / research fail-closed tests

```yaml
task_id: TP-CI-006
repo: pantheon
goal: Verify production adapters are fail-closed by default.
target_paths:
  - integrations/openclaw/tests/*
  - services/research/tests/*
  - services/policy-learning/tests/*
acceptance:
  - broker/live/capital binding off by default
  - OSS production adapters fail closed
```

---

## 19. P0-CI-BRIDGE-001 Implementation Note

`P0-CI-BRIDGE-001` lands the first two hard gates from this SD:

```text
TP-CI-001:
  implemented by scripts/check_execution_bridge.py
  covered by scripts/test_check_execution_bridge.py

TP-CI-002:
  implemented by scripts/check_task_targets.py
  covered by scripts/test_check_task_targets.py

workflow:
  .github/workflows/p0-bridge-guards.yml
```

The bridge authority report now includes:

```json
{
  "bridge_path": "pantheon/lean",
  "submodule_path": "lean",
  "bridge_remote": "ajoe734/pantheon-lean.git",
  "bridge_commit": "<pantheon/lean HEAD>",
  "pantheon_algo_base_present": true,
  "compose_exec_path_valid": true
}
```

The no-wrong-repo guard scans P0 task state and task packets for target-like fields
that point at `lean-platform` / `ajoe734/lean-platform`. Such a target is allowed
only when the same task record is explicitly marked:

```yaml
migration_only: true
adr_override: ADR-EXEC-001-revision
```

Verification run for this implementation:

```bash
python3 scripts/check_execution_bridge.py
python3 scripts/check_task_targets.py
python3 -m unittest scripts/test_check_execution_bridge.py scripts/test_check_task_targets.py
python3 -m py_compile scripts/check_execution_bridge.py scripts/check_task_targets.py scripts/test_check_execution_bridge.py scripts/test_check_task_targets.py
python3 scripts/ci_stage0.py validate
```

TP-CI-003 through TP-CI-006 remain separate implementation packets and are not
claimed by this task.

---

## 20. P0-HEALTH-001 Implementation Note

`P0-HEALTH-001` lands the staged health endpoint cleanup scan from this SD:

```text
TP-CI-004:
  implemented by scripts/check_health_endpoints.py
  covered by scripts/test_check_health_endpoints.py

workflow:
  .github/workflows/p0-bridge-guards.yml
```

The health endpoint report verifies that the shared helper exposes:

```text
/healthz
/livez
/readyz
/metrics
```

It also verifies that default compose uses `/readyz` and reports staged
`docker-compose.control.yml` / `docker-compose.exec.yml` legacy `__health__`
occurrences. The workflow runs in `warn` mode while cleanup is still pending.
After compose cleanup, the same script can be switched to `--mode fail` to make
any remaining staged legacy endpoint a CI failure.

Current scan result at implementation time:

```text
docker-compose.control.yml legacy __health__: 10
docker-compose.exec.yml legacy __health__: 5
```

Verification run for this implementation:

```bash
python3 scripts/check_health_endpoints.py --mode warn
python3 scripts/check_health_endpoints.py --mode fail
python3 -m unittest scripts/test_check_health_endpoints.py
python3 -m py_compile scripts/check_health_endpoints.py scripts/test_check_health_endpoints.py
```

Expected result: `warn` exits 0 with the legacy occurrence report; `fail`
exits 1 until the staged compose healthchecks migrate away from `__health__`.

TP-CI-003, TP-CI-005, and TP-CI-006 remain separate implementation packets and
are not claimed by this task.

---

## 21. Final Statement

This SD establishes CI as the enforcement layer for the current P0 truth:

```text
pantheon/lean is the current bridge.
paper baseline is expected.
live is fail-closed.
source/search are bounded.
research/OpenClaw production activations are off.
frontend demo usage must not leak into staging/prod.
```
