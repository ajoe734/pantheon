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





# SA-09 v2 — `lean-platform` 未對齊 / 非當前 Execution Repo 差異分析

## 1. 本章修正重點

本章取代原 SA-09。
最新 Codex 盤點已明確指出：

```text
lean-platform 已 clone，
但它不是目前 Pantheon 實際接的 Lean repo。
目前 Pantheon 接的是 pantheon/lean submodule，
remote 是 ajoe734/pantheon-lean.git。
```

因此 `lean-platform` 的狀態要從「可能是 execution substrate」修正為：

> **不是目前 Pantheon 實際 execution bridge；是未對齊 repo、歷史分支、或未來 migration / cherry-pick 候選。**

---

## 2. 原藍圖定位 vs 現況

### 2.1 原藍圖定位

Pantheon 母文件的 repo 落點索引中：

```text
lean-platform = Execution Substrate
```

承接：

```text
per-pool paper / canary / live runtime
orders / fills / positions / runtime health / broker events
```

### 2.2 最新現況

Codex 盤點：

```text
lean-platform branch = option auto exercise regression
rg Pantheon|PANTHEON|RuntimeBinding|SignalStore 沒有命中
Launcher/config.json 仍是標準 Lean config
```

這代表：

```text
lean-platform 並未包含目前 Pantheon bridge 訊號。
```

### 2.3 實際 bridge

```text
pantheon/lean submodule
remote = ajoe734/pantheon-lean.git
HEAD = 0ca2bdbd Add PantheonAlgoBase — Pantheon LEAN bridge
```

---

## 3. `lean-platform` 的新判定

| 判斷項 | 結論 |
|---|---|
| 是否是目前 Pantheon 實際 execution repo | 否 |
| 是否包含 PantheonAlgoBase bridge | 目前盤點未命中 |
| 是否包含 RuntimeBinding / SignalStore 等 Pantheon integration | 目前盤點未命中 |
| 是否仍是標準 Lean config | 是，依 Codex 盤點 |
| 是否應由 Codex 繼續 patch | 否，除非先通過 ADR / migration |
| 是否可作 migration target | 可以，但需要 cherry-pick / submodule 改指向 |
| 是否可保留為歷史 / vendor branch | 可以，但需明確標記 |

---

## 4. 這不是 execution 功能缺口，而是 repo mapping gap

以前可能會把 `lean-platform` 未整合解讀成：

```text
Execution Plane 沒做
```

現在應修正為：

```text
Execution bridge 做在 pantheon/lean，不在 lean-platform。
lean-platform 的問題是 repo mapping / blueprint alignment drift，不代表 current execution baseline 不存在。
```

這很重要，因為後續開發不能再把 `lean-platform` 當作 P0 patch target。

---

## 5. 主要風險

### 5.1 Codex patch 錯 repo

若 Codex 看到藍圖仍寫 `lean-platform`，可能會：

```text
在 lean-platform 補 RuntimeBinding
在 lean-platform 補 TelemetryEvent exporter
在 lean-platform 補 broker guard
```

但 Pantheon 實際使用的是 `pantheon/lean`，結果會變成：

```text
code exists in inactive repo
runtime still does not use it
```

### 5.2 文件與實作不一致

原藍圖仍寫：

```text
lean-platform = Execution Substrate
```

但現況是：

```text
pantheon/lean = actual bridge
```

這會導致 SA、SD、Codex task、CI、deployment 文件持續分歧。

### 5.3 migration 風險

如果未來決定重新讓 `lean-platform` 成為正式 execution repo，必須：

```text
從 pantheon-lean cherry-pick PantheonAlgoBase bridge
調整 .gitmodules
調整 docker-compose.exec.yml
調整 runtime_bootstrap 路徑
調整 CI
調整 deployment manifests
```

### 5.4 stale repo security risk

已 clone 但未使用的 repo 可能仍留有：

```text
old config
old broker placeholder
outdated dependency
confusing branch state
```

若 operator 或 Codex 誤用，會造成運維風險。

---

## 6. lean-platform 處置選項

### Option A — 明確退役 / Archive

適用條件：

```text
Pantheon 決定以 pantheon/lean / pantheon-lean 為正式 execution bridge。
```

動作：

```text
1. lean-platform README 加上 retired / not-current-runtime 標記。
2. 移除所有 Codex task target。
3. CI 加上 no-production-reference check。
4. 藍圖 repo mapping 更新成 pantheon/lean。
5. 若保留 repo，只作 historical / upstream experiment branch。
```

### Option B — 遷移回 lean-platform

適用條件：

```text
團隊希望保留 lean-platform 作 product fork，pantheon/lean 只是暫時 submodule。
```

動作：

```text
1. 將 pantheon/lean bridge commits cherry-pick 到 lean-platform。
2. 確認 PantheonAlgoBase、runtime context、paper runtime config 都存在。
3. .gitmodules 改指 lean-platform。
4. docker-compose.exec.yml 改路徑 / branch。
5. pantheon-lean 改成 archived 或 upstream adapter repo。
```

### Option C — 保留 lean-platform 為 migration candidate

適用條件：

```text
暫時不決定，但不希望 Codex 誤改。
```

動作：

```text
1. 在 SA / SD / task packet 明確標示 lean-platform = migration candidate only。
2. 不允許 P0 runtime work target lean-platform。
3. 待 ADR-EXEC-001 決定。
```

---

## 7. 建議 ADR 更新

### ADR-EXEC-001 應改名

原：

```text
Official Execution Substrate Repository
```

修正：

```text
Official Pantheon LEAN Bridge Repository and Submodule Policy
```

### 必填欄位

```text
current_bridge_path: pantheon/lean
current_bridge_remote: ajoe734/pantheon-lean.git
current_bridge_head: 0ca2bdbd Add PantheonAlgoBase — Pantheon LEAN bridge
inactive_repo: ajoe734/lean-platform
inactive_repo_status: cloned but not integrated
decision:
  - keep pantheon-lean
  - migrate to lean-platform
  - merge / rename
  - sidecar
```

---

## 8. Required Checks

### 8.1 Repo reference check

```bash
rg "lean-platform|pantheon-lean|/workspace/lean|submodule lean" .
```

### 8.2 No wrong target check

Codex task packet 必須有：

```yaml
official_execution_target:
  path: pantheon/lean
  remote: ajoe734/pantheon-lean.git
  not_target:
    - ajoe734/lean-platform
```

### 8.3 CI check

```text
CI should fail if:
  - runtime work targets lean-platform without ADR override
  - docker-compose.exec.yml no longer matches .gitmodules
  - PantheonAlgoBase missing from pantheon/lean
```

---

## 9. lean-platform gap table v2

| Gap ID | Gap | Type | Severity | Action |
|---|---|---|---|---|
| LP-GAP-001 | 舊稿曾把 lean-platform 視為 execution substrate；現已 canonicalize 為 pantheon/lean | Historical Repo Mapping Drift | Closed | 2026-05-03 後不再列為 active gap |
| LP-GAP-002 | lean-platform 無 Pantheon bridge 命中 | Historical Integration Gap | Superseded | 不作 current target |
| LP-GAP-003 | Codex 可能 patch 錯 repo | Process Guard | Low | task target guard 保留 |
| LP-GAP-004 | 如果未來要遷移，需 cherry-pick pantheon-lean bridge | Future Migration Only | Deferred | 需新 ADR 才能開工 |
| LP-GAP-005 | stale clone 可能造成 operator 混淆 | Operational Note | Low | label / archive 可作 hygiene，不列藍圖 gap |
| LP-GAP-006 | CI / compose 需保證指向 actual bridge | Verification Gap | High | submodule / compose CI |

---

## 10. 本章結論

`lean-platform` 的 SA 結論應從：

```text
可能未完成的 execution substrate
```

修正為：

```text
不是目前 Pantheon 實際接入的 execution bridge；
目前實際 bridge 在 pantheon/lean submodule / ajoe734/pantheon-lean.git。
lean-platform 的問題是藍圖落點與現行實作不一致，屬 repo mapping / migration gap。
```

最重要的下一步不是在 `lean-platform` 補 code，而是：

```text
1. 決定是否正式承認 pantheon/lean / pantheon-lean 為 execution bridge。
2. 更新藍圖 repo mapping。
3. 阻止 Codex 對 lean-platform 做錯誤 P0 runtime patch。
4. 若仍想用 lean-platform，制定 migration / cherry-pick / submodule rewrite plan。
```
