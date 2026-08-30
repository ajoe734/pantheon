# Pantheon 全系統盤點、GAP、SA、SD 與執行架構 — 2026-08-30

這份文件包把原始稽核、目前 repository/hosted evidence，以及後續 exact-head 設計稽核收斂成一個可執行但尚未宣稱完成的架構計畫。重點不是把每個失敗各開一個修補，而是消除造成重複路徑、假完成與廢碼累積的共同根因。

| 凍結項 | 值 |
|---|---|
| Pantheon 規劃基線 | `origin/dev@1095c55bf42acc91fac18b701cd24ad5b1874438` |
| execute-plans 規劃基線 | `origin/dev@bd03c863e3c2c1c64b9b7797f27cefaf84df17c1` |
| Hosted accepted pair | FE `bd03c863...` + BFF `e7f010dc...` |
| Hosted pairId | `6899d0daadb3dea2dbc3ae93456cf5818675dbd9a5c4284f676b80b5ce59c1a1` |
| GAP disposition | 19 active + OP-G03 baseline-closed |
| Catalog | 1 plan-freeze + 30 ownership-derived execution/support tasks；OP-G14 沿用既有 blocked execute-plans task |
| BFF baseline | 441 unique method+normalized-path rows、421 handlers、12 framework decorators |
| Catalog materialization | **blocked**；目前 bridge 尚未端到端保留 `target_repo` |

## 文件導覽

| 文件 | 唯一用途 |
|---|---|
| [FULL_OPERATION_AUDIT_2026-08-29.md](FULL_OPERATION_AUDIT_2026-08-29.md) | 原始完整盤點、測試快照、指令與限制。 |
| [CURRENT_GAP_DISPOSITION_2026-08-30.md](CURRENT_GAP_DISPOSITION_2026-08-30.md) | 判斷哪些敘述正確、哪些需限縮，以及每個 GAP 的唯一處置。 |
| [SA_GAP_REMEDIATION_2026-08-30.md](SA_GAP_REMEDIATION_2026-08-30.md) | 根因、目標權威面、bounded contexts 與架構不變量。 |
| [SD_GAP_REMEDIATION_2026-08-30.md](SD_GAP_REMEDIATION_2026-08-30.md) | 技術設計、逐 domain route ownership、caller cutover 與退役規格。 |
| [EXECUTION_DAG_2026-08-30.md](EXECUTION_DAG_2026-08-30.md) | 有理由的依賴、hot-file owner、capacity lock 與完成語意。 |
| [EXECUTION_TASK_CATALOG_2026-08-30.json](EXECUTION_TASK_CATALOG_2026-08-30.json) | 441 條 route、421 handlers、52 筆 command reference、167 筆 port-import inventory 與 task/artifact/dependency 的機器真相。 |

## 「正常運作」的補強定義

原報告的「程式碼、測試、CI、看板」四層是必要條件，但不足以排除兩套都能跑、假 fallback 成功、或 source 綠而 hosted 無效果。完整判定需同時滿足：

1. production entrypoint 有自然 caller，且不是 stub/mock。
2. 每種 mutation 只有一個 write authority；read projection 可追溯到它。
3. 成功有 durable same-ID/version readback，重啟與多副本後仍成立。
4. 重試、併發、依賴故障、SSE replay 與 rollback 語意正確且 fail-closed。
5. 正確 topology 的測試實際執行；skip、timeout、缺 DB/網路不算 pass。
6. 安全關鍵流程經正式治理路徑驗證，不靠 fixture bypass。
7. CI、部署、manifest、image 與 exact FE/BFF SHA 綁定，缺 gate 即阻擋。
8. task/git/deployment/caller inventory 一致；cutover 完成後舊 implementation、shim、mount、config 與專屬 tests 同批刪除。

因此現在只能判定「核心已真實實作，accepted read-only pair 部分可用」，不能判定「全系統正常」。F22 安全證明與 F25 merge-enforcement 仍是獨立 unresolved risks。

## 根因導向結論

- BFF 不是按 URL 世代切成 `legacy`/新版兩棵 router，而是 441 條 route 逐條歸到 17 個既有或具名 bounded-context owners。所有 421 個 handler 另有唯一 implementation owner。
- `sem_final_generic_read_alias` 的三個 decorators 跨 Governance/Research；先抽成一個無 router、無 store 的 typed service，再由兩個 domain 提供薄 wrapper，禁止複製 handler。
- `main.py` 只由 assembly task 修改，且必須等活動中的 `AGORA-PERSONA-DURABLE-LIST-READBACK-V2-20260830` 合併後 rebase。
- `ports/` 是唯一 BFF shared-port public/implementation namespace；六個 `domain_ports/*.py` implementation 併入同名 `ports/*.py` 後整棵刪除，禁止第三套 port namespace。
- 第二中央命令面按 caller cutover → main cutover → implementation/shim/mount/test delete 的順序退役；BFF `command_executor.py` 保留。
- Source 沒有證據支持新的 source-code cleanup task。OP-G12 只做 hosted effect proof；既有 reconcile-only/manual bounded one-shot 不重寫。
- OP-G14 沿用既有 `AGORA-AGC-14-HOSTED-DEMO-AUTHENTIC-V5-20260829`；其 artifacts 已落在 execute-plans，不建立第二個 hosted spec/evidence task。
- 28 個 ACG 與 4 個 PFG terminal rows 全部保留 immutable terminal fact，catalog 只接 current residual；既有 4 個 nonterminal tasks 逐筆列出且不複製 scope。
- 跨 repo materialization 先修 bridge 的 `target_repo` 不可變鏈，再派其他任務；resolver dry-run 不冒充 canonical readback。

## 凍結規則

- Catalog 逐條列出 441 個 `method + normalized_path`，每條一個 route owner；每個 source handler 一個 implementation owner。
- Wave 1 route tasks 不得修改 `services/control-plane/bff/main.py`。
- 每個 artifact 只能屬於一個 catalog task；外部活動 task 與 main assembly 的重疊用顯式 dependency 解決。
- 沒有 proven GAP 的 cleanup 不預先授權；退役只有在 active caller/import/config/test 掃描為零後完成。
- Catalog 尚未 materialized/read back，實作不得把 JSON 的 `target_repo` 欄位當作已派工證據；既有 `AGORA-AGC-14-HOSTED-DEMO-AUTHENTIC-V5-20260829` 不重新 materialize。
- F22、F23、F25 保留可見，但本 functional-first 計畫不加入 MFA/security bypass 或虛構完成宣稱。
