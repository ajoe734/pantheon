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





# SA-14 v2 — Execution Substrate 偏移專章：`lean-platform` → `pantheon/lean` Submodule

## 1. 本章修正重點

本章取代原 SA-14。
原 SA-14 的核心問題是「`Lean` vs `lean-platform`」。
最新 Codex 盤點讓問題變得更精確：

```text
藍圖指定:
  lean-platform = Execution Substrate

現況實際接入:
  pantheon/lean submodule
  remote = ajoe734/pantheon-lean.git
  HEAD = Add PantheonAlgoBase — Pantheon LEAN bridge

未對齊:
  lean-platform
```

因此本章改名為：

> **Execution Substrate Drift: from blueprint `lean-platform` to actual `pantheon/lean` submodule / `pantheon-lean`.**

---

## 2. 偏移不是「Lean 有沒有」，而是「正式 bridge repo 是誰」

目前不是沒有 LEAN bridge。
Codex 盤點顯示已經有：

```text
pantheon/lean submodule
PantheonAlgoBase
docker-compose.exec.yml 指向 /workspace/lean/Launcher/config.json
runtime_bootstrap.py
paper runtime baseline
```

真正問題是：

```text
設計藍圖與 repo mapping 還寫 lean-platform；
實際 bridge 在 pantheon/lean / pantheon-lean。
```

所以「偏移」的本質是：

```text
Repository authority drift
Submodule authority drift
Documentation drift
Codex target drift
CI/deployment target drift
```

---

## 3. 新的 execution topology

### 3.1 現行拓撲

```text
pantheon repo
  ├─ .gitmodules
  │    └─ lean/ → ajoe734/pantheon-lean.git
  ├─ docker-compose.exec.yml
  │    └─ /workspace/lean/Launcher/config.json
  └─ services/execution/lean_runtime/runtime_bootstrap.py
       ├─ paper role → Python paper runtime
       └─ live role → health-only sidecar placeholder

pantheon/lean
  └─ pantheon_algo/base.py → PantheonAlgoBase bridge

lean-platform
  └─ cloned but not current target
```

### 3.2 與藍圖拓撲差異

藍圖拓撲：

```text
pantheon
→ lean-platform
→ LEAN paper/canary/live runtime
```

現行拓撲：

```text
pantheon
→ pantheon/lean submodule / pantheon-lean
→ runtime_bootstrap paper baseline
→ live health-only placeholder
```

---

## 4. Execution maturity 的新分級

| 層級 | 狀態 |
|---|---|
| L0 文件 / 藍圖 | 已有 |
| L1 submodule bridge 接入 | 已有 `pantheon/lean` |
| L2 PantheonAlgoBase bridge | 已有 |
| L3 paper runtime baseline | 已有 |
| L4 DeploymentPlan / RuntimeBinding 完整 launch contract | 需驗證 / 需補 |
| L5 full Lean Launcher production kernel | 未完成 |
| L6 broker SDK canary/live execution | 未完成 |
| L7 production telemetry / reconciliation / incident / evolution | 部分 schema，有待閉環 |
| L8 HA production topology | deferred |

這比原本「Lean 未驗證」更準。現在應判斷為：

> **Execution foundation 已有，但 production-grade execution substrate 尚未完成。**

---

## 5. Intentional Gap vs Actual Gap

本章要區分：

### 5.1 Intentional / Safety Deferral

```text
live role 是 health-only sidecar
research/learning adapters require data/model posture and cannot route directly to orders
OpenClaw 不作 live broker kernel
BFF HA/LB defer
dev compose 保留 JSON/JSONL fallback
```

這些不一定是 bug，而是安全與分階段策略。

### 5.2 Actual Gap

```text
藍圖 repo mapping 未更新
lean-platform 未對齊現行 bridge
RuntimeBinding / DeploymentPlan 與 pantheon/lean bridge 的 contract 需明確
bracket order log-only
full Lean Launcher production path 未完成
frontend demo islands
health endpoint cleanup
```

### 5.3 Production Adoption Gap

```text
前端 demo auth
BFF HA/LB 未做
staging compose legacy health endpoint
production Postgres ownership 未全面 default
```

---

## 6. 主要偏移項目

| Drift ID | Drift | Current | Required |
|---|---|---|---|
| EXD-001 | historical repo mapping drift | canonicalized to pantheon/lean / pantheon-lean on 2026-05-03 | closed; keep as history only |
| EXD-002 | submodule authority | pantheon/lean remote pantheon-lean | keep formal submodule policy |
| EXD-003 | runtime maturity drift | paper baseline exists; live placeholder | mark production live incomplete |
| EXD-004 | launch contract drift | compose points config; formal RuntimeBinding contract unclear | DeploymentPlan → runtime_bootstrap contract |
| EXD-005 | broker execution drift | bracket order log-only | guarded broker execution |
| EXD-006 | telemetry producer drift | schema exists; bridge telemetry needs proof | paper runtime TelemetryEvent |
| EXD-007 | lean-platform historical clone | not current runtime target | do not create active work unless future migration ADR |
| EXD-008 | task target guard | Codex may patch wrong repo if old docs are read | keep guard; not a blueprint gap |

---

## 7. ADR-EXEC-001 v2

### 7.1 Title

```text
ADR-EXEC-001 — Official Pantheon LEAN Bridge Submodule and Execution Repo Policy
```

### 7.2 Context

```text
Older Pantheon blueprint text named lean-platform as Execution Substrate.
Current Pantheon repo references submodule lean/.
Submodule remote is ajoe734/pantheon-lean.git.
pantheon/lean includes PantheonAlgoBase bridge.
2026-05-03 decision: use pantheon/lean / pantheon-lean as the official bridge; lean-platform is historical / non-target unless a future migration ADR says otherwise.
```

### 7.3 Decision Options

#### Option A — Keep pantheon/lean / pantheon-lean as official bridge

```text
Pros:
  matches actual implementation
  least migration cost
  preserves current compose contract

Cons:
  blueprint repo mapping must change
  lean-platform must be retired or reclassified
```

#### Option B — Migrate bridge into lean-platform

```text
Pros:
  matches original blueprint name

Cons:
  requires cherry-pick/migration
  high chance of missing current bridge details
```

#### Option C — Rename / merge into `pantheon-lean-runtime`

```text
Pros:
  removes ambiguity
  name matches product role

Cons:
  high repo migration cost
```

#### Option D — Keep pantheon-lean bridge but introduce sidecar

```text
Pros:
  bridge remains thin
  production launch/telemetry contract centralized

Cons:
  sidecar becomes critical runtime component
```

### 7.4 Recommended near-term decision

```text
Adopt Option A for near-term:
  pantheon/lean / pantheon-lean is current official bridge.

Then evaluate Option D:
  sidecar for production live execution / telemetry / broker guards.
```

---

## 8. Revised runtime roadmap

### P0 — Solidify current bridge

```text
1. Document pantheon/lean as current bridge.
2. Add bridge metadata endpoint / file.
3. Verify PantheonAlgoBase context fields.
4. Define DeploymentPlan → runtime_bootstrap contract.
5. Ensure live placeholder is fail-closed.
6. Add paper runtime TelemetryEvent heartbeat.
```

### P1 — Convert bridge to governed paper runtime

```text
1. RuntimeBinding injection.
2. Artifact id/version/checksum propagation.
3. Capital pool id propagation.
4. Paper orders / fills / pnl telemetry.
5. Reconciliation-ready event stream.
```

### P2 — Canary/live activation preparation

```text
1. Broker SDK execution path.
2. Bracket order from log-only to guarded execution.
3. Stage-aware credentials.
4. RiskPolicy veto before broker connection.
5. Canary budget constraints.
6. Rollback / pause / liquidate bridge.
```

### P3 — Production topology

```text
1. BFF HA/LB if no longer deferred.
2. Full durable Postgres ownership.
3. object-store artifact projection.
4. production monitoring.
5. production incident / evolution action.
```

---

## 9. CI / Verification changes

### 9.1 Submodule consistency test

```text
assert .gitmodules contains lean path
assert lean remote == ajoe734/pantheon-lean.git
assert pantheon/lean contains pantheon_algo/base.py
assert docker-compose.exec.yml maps /workspace/lean
```

### 9.2 Wrong repo guard

```text
fail CI if P0 runtime task targets ajoe734/lean-platform without ADR override
```

### 9.3 Runtime bootstrap test

```text
paper role starts paper runtime
live role remains health-only unless activation flag explicitly enabled
live role cannot place broker order in placeholder mode
```

### 9.4 Bridge telemetry test

```text
PantheonAlgoBase / paper runtime emits heartbeat with:
  runtime_id
  runtime_binding_id if available
  artifact_id if available
  deployment_stage=paper
```

---

## 10. 修正版結論

Execution Substrate Drift 的正確結論是：

> **Pantheon 目前不是沒有 LEAN bridge；它已有 `pantheon/lean` submodule / `pantheon-lean.git` bridge，並且具備 truthful paper runtime baseline。真正差異是：原藍圖把 execution substrate 寫成 `lean-platform`，但實際 runtime bridge 已轉到 `pantheon/lean`；此外目前 execution maturity 仍停在 paper baseline / health sidecar / log-only bracket order 階段，尚未達完整 Lean Launcher + broker SDK production kernel。**

因此 SA-14 v2 的優先順序：

```text
1. 先更新 repo authority：pantheon/lean 是 current bridge。
2. 再補 DeploymentPlan / RuntimeBinding / telemetry contract。
3. 保持 live fail-closed。
4. 最後再決定 lean-platform 是退役、遷移、還是合併。
