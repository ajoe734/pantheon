# LP-005 RL Path 審查意見（Codex）

**任務**: `LP-005`  
**作者**: Grok  
**審查者**: Codex  
**狀態**: Changes requested

## 結論

這一輪不能通過。`services/learning/rl/` 已經把「何時該用 RL」和大致訓練流程寫出來了，但目前至少還有四個會直接影響架構對齊或下游可落地性的問題：

1. promotion / lifecycle vocabulary 與 canonical registry state 不一致
2. RL artifact model 和 LEAN 載入路徑繞開了 `REG-001` / `REG-003` / `EX-001` 的治理 metadata
3. `RS-003` 在文件中被寫成「訓練後 RL policy replication gate」，但目前 repo 內的 `RS-003` 實際上是「RS-002 research candidate 進 registry 前」的 gate
4. README 宣稱的 example/config/script 目前不存在，`links and references valid` 這條驗收還不能算達成

## Findings

### 1. RL 文件使用了另一套 promotion stage，會讓 registry / loader 接錯 enum

`PATH_DEFINITION.md` 和 `README.md` 現在都把 RL policy 的 promotion flow 寫成：

- `development -> staging -> approved -> production`

對應位置：

- `services/learning/rl/PATH_DEFINITION.md:265`
- `services/learning/rl/PATH_DEFINITION.md:328`
- `services/learning/rl/README.md:63`
- `services/learning/rl/README.md:89`

但 canonical registry contract 的 lifecycle state 是：

- `draft`
- `candidate`
- `paper`
- `live`
- `retired`

對應位置：

- `services/registry/contract.md:47`
- `services/registry/contract.md:84`
- `services/execution/artifact-loader/contract.md:53`

這不是命名風格差異而已。`EX-001` loader 會直接用 `promotion_state` 做 allow / reject，`paper` mode 只接受 `paper`，`live` mode 只接受 `live`。如果 `LP-005` 繼續輸出 `staging` / `approved` / `production`，下游不是要額外做不該存在的 alias，就是會直接拒收。

### 2. RL artifact example 與 LEAN handoff 繞過了 registry projection / Object Store 契約

`PATH_DEFINITION.md` 的 RL artifact example 目前是自訂欄位：

- `id`
- `type`
- `model_uri`
- `config_uri`
- `promotion_status`

對應位置：

- `services/learning/rl/PATH_DEFINITION.md:221`

但 `REG-001` / `REG-003` / `EX-001` 目前要求的最小治理欄位至少包含：

- `registry_id`
- `artifact_type`
- `strategy_id`
- `version`
- `lifecycle_state` / execution projection 的 `promotion_state`
- `lineage`
- `checksum`
- `storage_ref`
- `rollback_target` 或 `rollback`
- 後續 `LP-003` 的 `experiment_refs`

對應位置：

- `services/registry/contract.md:74`
- `services/registry/contract.md:114`
- `services/registry/lineage/contract.md:33`
- `services/execution/artifact-loader/contract.md:72`

同一個偏移也出現在 LEAN integration 段落。文件現在寫的是 `RLPolicyExecutor(policy_uri, config_uri)` 直接吃 URI，甚至 README 的 quickstart 也是直接把 artifact 丟到 `s3://...` 再 submit。

對應位置：

- `services/learning/rl/PATH_DEFINITION.md:372`
- `services/learning/rl/README.md:92`
- `services/learning/rl/README.md:172`

但 `EX-001` 的明確規則是：

- loader 必須先驗治理 metadata
- transport 走 LEAN-native `ObjectStore`
- execution 端不應靠 direct S3 / GCS / local injection bypass governance

對應位置：

- `services/execution/artifact-loader/contract.md:33`
- `services/execution/artifact-loader/contract.md:74`
- `services/execution/artifact-loader/contract.md:86`

所以 `LP-005` 不能把 runtime contract 定義成「LEAN 直接拿兩個 URI 就載模型」。正確做法應該是描述：

1. RL training 產出 governed registry entry / promoted metadata
2. registry / promotion tooling materialize `metadata.json` + `artifact.bin` 到 Object Store
3. execution 透過 artifact loader 驗 metadata 後再把 payload 交給 `RLPolicyExecutor`

### 3. `RS-003` 在 RL 文件中的位置和職責被寫歪了

現在 `LP-005` 一方面說 RL candidate selection 的輸入是「已通過 `RS-003` 的 research」：

- `services/learning/rl/PATH_DEFINITION.md:68`

但後面又把 `RS-003` 重寫成「RL policy 訓練完成後，拿 policy 本身去做 first-pass replication gate」：

- `services/learning/rl/PATH_DEFINITION.md:285`
- `services/learning/rl/PATH_DEFINITION.md:420`
- `services/learning/rl/README.md:46`
- `services/learning/rl/DECISION_TREES_AND_EDGE_CASES.md:255`

這和 repo 內已完成的 `RS-003` 不一致。當前 `RS-003` README 寫得很清楚，它驗的是：

- `RS-001 -> RS-002` 產出的 research candidate / `StrategySpec`
- 通過後才進 `REG-001`

對應位置：

- `services/research/replication/README.md:11`
- `services/research/replication/README.md:185`
- `services/research/replication/README.md:212`

也就是說，`RS-003` 目前不是「訓練後 policy 驗證器」。`LP-005` 如果要沿用現有 `RS-003`，應該把語義改成：

- `RS-003` 先把 research-normalized strategy / proposal 送進 registry candidate path
- RL training 是在這之後的 learning branch
- 訓練出來的 policy artifact 再走 `REG-001` / `REG-002` / `EX-001` 對應的 evaluation + promotion checks

不然下游實作者會以為要把一個已存在的 research gate 擴充成另一個完全不同的 post-training gate。

### 4. README 的 example / quickstart 目前是斷的，驗收中的 reference validity 不成立

README 現在明寫：

- 有 `services/learning/rl/examples/config_ppo_portfolio.yaml`
- 可以直接跑 `scripts/fetch_training_data.py`
- `scripts/train_rl_policy.py`
- `scripts/evaluate_rl_policy.py`
- `scripts/registry_submit.py`

對應位置：

- `services/learning/rl/README.md:75`
- `services/learning/rl/README.md:132`
- `services/learning/rl/README.md:145`
- `services/learning/rl/README.md:154`
- `services/learning/rl/README.md:163`
- `services/learning/rl/README.md:172`

但目前磁碟上 `services/learning/rl/` 只有四個 markdown 檔，沒有 `examples/` 目錄，也沒有上述腳本。

這代表 README 底部自己列的 approval criteria：

- `All links and references are valid`

目前不能打勾。這一點雖然不像 state mismatch 那麼核心，但它會直接誤導後續 smoke-test 與 integration ownership。

## 建議修正順序

1. 先把所有 lifecycle / promotion 用語收斂到 `draft/candidate/paper/live/retired`
2. 把 RL artifact example 改寫成與 `REG-001` / `REG-003` / `EX-001` 相容的 metadata envelope，並補上 Object Store projection 說明
3. 重新界定 `RS-003` 在 RL path 的角色，避免把既有 research gate 寫成 post-training RL gate
4. 刪掉不存在的 quickstart 指令 / example 檔，或真的補出最小可引用的 placeholder artifacts

## Reviewer Decision

`LP-005` 應退回作者修正，修完後再進 review。
