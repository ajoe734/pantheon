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





# SA-08 v2 — `pantheon/lean` / `pantheon-lean` 實際 Execution Bridge 差異分析

## 1. 本章修正重點

本章取代原 SA-08。原本 SA-08 將 `Lean` 視為目前實際 execution substrate，並將 `lean-platform` 視為未對齊分支。
依據最新 Codex 盤點，這個判斷還需要再精細化：

```text
目前 Pantheon 實際接的是：
  pantheon/lean submodule

該 submodule remote 是：
  ajoe734/pantheon-lean.git

目前 bridge 證據：
  pantheon/lean HEAD = 0ca2bdbd Add PantheonAlgoBase — Pantheon LEAN bridge
  pantheon_algo/base.py 含 PantheonAlgoBase bridge
  docker-compose.exec.yml 指向 /workspace/lean/Launcher/config.json

不是目前實際接的：
  ajoe734/lean-platform.git
```

因此，本章的正確分析對象不是泛稱 `Lean`，而是：

> **`pantheon/lean` submodule / `ajoe734/pantheon-lean.git`，也就是目前 Pantheon 實際引用的 LEAN bridge repo。**

---

## 2. 新的 repo 定位

| Repo / Path | 新定位 | 說明 |
|---|---|---|
| `pantheon/lean` submodule | **Current Pantheon LEAN bridge** | 目前 Pantheon 實際接入的 LEAN bridge，remote 是 `ajoe734/pantheon-lean.git` |
| `ajoe734/pantheon-lean.git` | **Actual bridge remote** | 包含 `PantheonAlgoBase` 類 bridge 訊號 |
| `ajoe734/lean-platform` | **Not current runtime target** | 已 clone，但不是目前 Pantheon 實際接的 repo；Codex 盤點顯示未命中 Pantheon bridge 關鍵字 |
| generic upstream Lean | **Engine substrate concept** | LEAN engine 的上游 / 基底概念，不等於目前產品 bridge |
| `services/execution/lean_runtime/runtime_bootstrap.py` | **Current execution bootstrap** | Python slim execution container entrypoint，負責目前 paper baseline / health sidecar |

---

## 3. 與原 SA-08 的差異

### 3.1 原判斷

```text
Lean = 實際被修改 / 可能承接 execution substrate
lean-platform = 未實際採用 / 歷史分支
```

### 3.2 修正後判斷

```text
pantheon/lean submodule / pantheon-lean.git = 目前實際 Pantheon LEAN bridge
lean-platform = 未對齊 / 不在目前 Pantheon runtime path
generic Lean = engine substrate，不等於具 Pantheon bridge 的產品 repo
```

### 3.3 影響

這個修正會影響後續所有 execution 類文件：

```text
SA-08: 不能再泛稱 Lean，應分析 pantheon/lean / pantheon-lean。
SA-09: lean-platform 是未對齊 repo，不是目前 execution gap 的主體。
SA-14: Execution substrate drift 應寫成 blueprint lean-platform vs actual pantheon/lean submodule。
SA-18: CI / tests 應驗證 pantheon/lean submodule，而非 ajoe734/Lean 或 lean-platform。
SA-19: Gap matrix 中 repo ownership 要改成 pantheon-lean。
SA-20: 風險 register 要新增 submodule drift / detached bridge risk。
```

---

## 4. 目前 bridge 已完成的部分

依據 Codex 盤點，目前可認定已具備以下基礎：

### 4.1 Pantheon bridge repo 已接入

```text
.gitmodules line 1 指向 submodule lean/
remote = ajoe734/pantheon-lean.git
```

這表示 Pantheon 主 repo 不是任意讀某個外部 LEAN repo，而是已透過 submodule 固定接入目前 bridge。

### 4.2 PantheonAlgoBase 已存在

```text
pantheon/lean HEAD = 0ca2bdbd Add PantheonAlgoBase — Pantheon LEAN bridge
pantheon_algo/base.py line 42 有 Pantheon bridge
```

這是比原 SA 更強的證據。原本我們只能說「Lean integration 未驗證」；現在應修正為：

```text
Pantheon LEAN bridge 已存在，但 bridge 深度與 production runtime 完整度仍需評估。
```

### 4.3 compose execution path 已指向 submodule

```text
docker-compose.exec.yml line 146 指向 /workspace/lean/Launcher/config.json
```

這代表 staging / exec compose 層面已經知道要使用 `/workspace/lean`，而不是 `lean-platform` clone。

### 4.4 truthful paper runtime baseline 已存在

Codex 指出：

```text
execution Dockerfile 是 Python slim
啟動 services/execution/lean_runtime/runtime_bootstrap.py
paper role 啟動 Python paper runtime
live placeholder 是 health-only sidecar
```

因此目前 execution plane 不是空的。它有：

```text
paper baseline
health sidecar
runtime bootstrap
Pantheon LEAN bridge submodule
```

---

## 5. 仍未完成的 execution 能力

即使 bridge 已存在，仍不能判定完整 production execution runtime 已完成。

### 5.1 不等於完整 Lean Launcher production kernel

目前 execution container 是 Python slim，啟動 `runtime_bootstrap.py`。
這表示目前 runtime path 更像：

```text
Pantheon Python runtime bootstrap
→ paper role: Python paper runtime
→ live role: health-only placeholder
→ lean submodule available / config mounted
```

而不是：

```text
Pantheon DeploymentPlan
→ full Lean Launcher
→ broker SDK
→ live execution kernel
→ orders / fills / positions
→ canonical telemetry
```

### 5.2 live sidecar 是 health-only placeholder

如果 live role 仍是 health-only sidecar，則：

```text
live readiness = false
canary readiness = not proven
broker SDK production execution = not complete
```

### 5.3 bracket order 是 log-only

Codex 指出 `executor.py line 150` 風控 bracket order 仍是 log-only。
這代表：

```text
risk management surface exists,
but position-affecting bracket execution not yet production-grade.
```

### 5.4 RuntimeBinding / DeploymentPlan semantics 需驗證

目前已有 bridge，但仍需確認：

```text
PantheonAlgoBase 是否接收 runtime_binding_id？
DeploymentPlan 是否 materialize 成 runtime launch config？
paper runtime telemetry 是否帶 binding / plan / capital pool / artifact？
live placeholder 是否禁止誤用？
```

---

## 6. Execution bridge maturity assessment

| 能力 | 現況 | 判斷 |
|---|---|---|
| submodule 接入 | 已有 `pantheon/lean` 指向 `pantheon-lean.git` | 已完成 |
| Pantheon bridge class | 已有 `PantheonAlgoBase` | 已完成初版 |
| compose runtime path | exec compose 指 `/workspace/lean/Launcher/config.json` | 已對齊 |
| paper runtime | Python paper runtime baseline | 已具備 |
| live runtime | health-only sidecar placeholder | 未完成 |
| full Lean Launcher production | 未證明 | 未完成 |
| broker SDK production execution | 未證明 | 未完成 |
| bracket orders | log-only | 未完成 |
| RuntimeBinding injection | 需驗證 | 未完成 / 未確定 |
| TelemetryEvent exporter | 需驗證 | 未完成 / 未確定 |
| canary/live segregation | 需驗證 | 未完成 / 未確定 |

---

## 7. 關鍵差異重新表述

原 SA 的表述：

```text
Lean runtime integration unverified.
```

應修正為：

```text
Pantheon 已有 pantheon-lean bridge 與 truthful paper runtime baseline；
但目前 execution plane 尚未升級到完整 Lean Launcher + broker SDK production execution kernel。
DeploymentPlan / RuntimeBinding / TelemetryEvent / canary-live broker execution 的完整鏈條仍需補齊。
```

這是更準確的成熟度判斷。

---

## 8. 對藍圖的偏差判斷

藍圖要求 Execution Plane 包含：

```text
Runtime Manager
Artifact Loader
Runtime Binding Store
LEAN Paper Runtime
LEAN Canary Runtime
LEAN Live Runtime
Broker / Exchange / Subaccounts
Pause / Liquidate / Replace Actions
```

目前現況較接近：

```text
Runtime bootstrap baseline
Pantheon LEAN bridge submodule
Paper runtime baseline
Health-only live placeholder
部分風控 / bracket order log-only
```

因此差異是：

| Blueprint requirement | Current state | Gap |
|---|---|---|
| LEAN Paper Runtime | 有 truthful baseline | 基本對齊 |
| LEAN Canary Runtime | 未證明 | 缺 |
| LEAN Live Runtime | health-only sidecar | 缺 production kernel |
| Broker / Exchange / Subaccounts | 未證明 | 缺 production broker execution |
| RuntimeBinding Store | 需驗證 | 可能部分有，但未確認完整 |
| Artifact Loader | bridge 有雛形 | approved artifact loading 需確認 |
| Pause / Liquidate / Replace | live placeholder / bracket log-only | 缺 production action |
| Telemetry / Runtime Health | health baseline 可能有 | canonical TelemetryEvent 需確認 |

---

## 9. 對 Codex task 的修正

所有 execution 類 task packet 應改成：

```text
Target repo/path:
  pantheon/lean submodule
  remote: ajoe734/pantheon-lean.git

Do NOT target:
  ajoe734/lean-platform.git
```

除非 ADR 決定要 migrate。

### 9.1 新增必備 task

```text
TP-EXEC-001 Confirm pantheon/lean submodule as current execution bridge
TP-EXEC-002 Add bridge metadata to repo docs
TP-EXEC-003 Verify PantheonAlgoBase runtime context fields
TP-EXEC-004 RuntimeBinding → PantheonAlgoBase injection
TP-EXEC-005 DeploymentPlan → runtime_bootstrap launch contract
TP-EXEC-006 paper runtime TelemetryEvent exporter
TP-EXEC-007 live placeholder guard: fail-closed for live broker actions
TP-EXEC-008 bracket order transition from log-only to guarded execution
TP-EXEC-009 canary/live activation criteria
```

---

## 10. 修正版 Gap Table

| Gap ID | Gap | Revised Type | Severity | Notes |
|---|---|---|---|---|
| PLEAN-GAP-001 | official execution bridge 是 pantheon/lean，但藍圖仍寫 lean-platform | Repo Mapping Drift | Critical | 需 ADR / docs update |
| PLEAN-GAP-002 | paper runtime baseline 已有，但 production live kernel 未完成 | Execution Maturity | Critical | 不是空白，是未 productionized |
| PLEAN-GAP-003 | live placeholder 是 health-only sidecar | Intentional / Safety Gap | High | 應 fail-closed |
| PLEAN-GAP-004 | bracket order log-only | Production Execution Gap | High | 需 guarded execution |
| PLEAN-GAP-005 | RuntimeBinding injection 需驗證 | Contract Gap | High | telemetry / lineage pivot |
| PLEAN-GAP-006 | DeploymentPlan → runtime_bootstrap contract 需明確 | Contract Gap | High | runtime bootstrap 不能自由啟動 |
| PLEAN-GAP-007 | TelemetryEvent exporter 需驗證 | Telemetry Gap | High | paper baseline 也需 telemetry |
| PLEAN-GAP-008 | canary/live activation 未完成 | Intentional Deferral / Execution Gap | High | 符合安全方針，但需明示 |
| PLEAN-GAP-009 | lean-platform 未對齊 current bridge | Repo Drift | Medium-High | migration candidate |
| PLEAN-GAP-010 | full Lean Launcher + broker SDK production path 未完成 | Production Runtime Gap | Critical | live readiness blocker |

---

## 11. 本章結論

修正後，SA-08 的結論應改為：

> **Pantheon execution plane 已比原先 SA 判斷更成熟：它不是完全未接 Lean，而是已透過 `pantheon/lean` submodule / `pantheon-lean.git` 接入 Pantheon LEAN bridge，且已有 PantheonAlgoBase 與 paper runtime baseline。真正缺口不是「有沒有 Lean bridge」，而是「這個 bridge 是否已升級為完整 production LEAN Launcher + broker SDK runtime，並完整支援 DeploymentPlan、RuntimeBinding、TelemetryEvent、canary/live、pause/liquidate/replace」。**

目前狀態：

```text
Bridge baseline: 有
Paper runtime: 有
Production live runtime: 未完成
Canary/live activation: 未完成
lean-platform alignment: 未完成
Repo mapping clarity: 需要 ADR
```
