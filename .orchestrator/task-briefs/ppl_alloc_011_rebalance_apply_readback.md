# PPL-ALLOC-011 — Rebalance apply terminal execution + authoritative capital readback + restart-safe persistence

## 問題（來源：PPL-ALLOC-009 hosted 驗收 blocker，2026-07-13）

閉環驗收證據 `docs/04/pantheon_persona_promotion_allocation_gap_2026-07-07/archive/PPL-ALLOC-009-HOSTED-EVIDENCE-2026-07-13.json`
顯示 allocation 路徑的 policy 評估、proposal 持久化、human-approval 准入 gate 都通過，
但 **approved apply 從未真正執行到權威資本狀態**：

1. **Apply 不落地**：`POST /bff/rebalances/{id}/apply`（帶 approval ref）回 202，
   command 進 `bff_action_adapter` 後回 `executed` 收據，但 proposal 仍
   `status=pending / applied=false`，capital pool/sleeve 權威讀不到 target weight。
   `readback_source=local_snapshot`、`readback_status=degraded`、
   `authoritative_capital_readback=false`。第一輪 probe 的 command
   `7a3e7310-…` 甚至永遠停在 `submitted`。
2. **Restart 不安全**：`restart_persistence_preserved=false` —— dev 重新部署後
   `rb-20260713-001` 讀回 404。proposal/command 狀態是 in-memory，重啟即蒸發。

這正是 PPL-ALLOC-009 closeout 的硬 blocker（`Capital Pool / Execution Plane
write contract, restart-safe proposal authority, and post-apply allocation
readback remain blocking evidence`）。

## 目標

1. Approved rebalance apply 走到終態：apply command 執行後，rebalance 權威讀
   （`GET /bff/rebalances/{id}`）呈現 `applied=true`、approval reference、
   apply receipt/audit ref；失敗時呈現可診斷的 failed 終態，不停留在 submitted。
2. Apply 後 capital 權威讀（`/bff/capital-pools/{id}` 或 sleeve/binding read）
   反映 target weight（current_weight 更新或明確的 pending-settlement 狀態機），
   `authoritative_capital_readback=true` 等級的證據可由 curl 取得。
3. Rebalance proposal / apply command / 決策收據 restart-safe：BFF 重啟（dev
   redeploy）後 proposal 與 receipt 仍可讀（落 SQLite/Postgres/檔案任一 durable
   store，遵循現有 BFF persistence 慣例；不可只留 in-memory）。
4. 安全邊界不得回退：無 approval 的 apply 仍 409；emergency containment 仍
   不可 promote/increase；human-approval gate 語意不變。
5. 順手補：BFF 曝露部署身分（`/bff/version` 或等效，含 source commit SHA），
   解決 closeout「BFF 無 version endpoint 可對 deployed SHA」的證據缺口（小項）。

## 補充（2026-07-13 12:37Z 二簽證明後）

二簽 containment 准入已由 Human/Ops 補證完成（見
`.orchestrator/reviews/PPL-ALLOC-009-TWO-MAN-PROOF-2026-07-13.json`：
428→bound token→409 TWO_MAN→第二簽名者紀錄→202 admitted→command executed，
receipt `cmd-9febfc72bfa64624b7e0b495e7a79126`）。但 receipt 顯示
`dispatch_path=bff_action_adapter`、`entity_type/entity_id=null`，且 persona
post-state readback 仍 `paper_running` 未反映 freeze——**containment 的終態執行
與 post-state readback 跟 rebalance apply 是同一個 adapter 缺口**，一併納入
本任務目標 1/2 的範圍：safe containment 執行後，persona 權威讀應呈現
frozen（或明確的 containment 狀態），且 receipt 帶 entity/audit 參照。

## 邊界

- 不動 supervisor/poll cadence。
- paper ledger 與真實資金池語意區隔不得混淆；本任務只處理 governed rebalance
  的 dev 權威狀態機，不接真 broker。
- 走標準 git workflow：off dev 開 task branch、trailers、scope-check、PR。
- 與 PPL-ALLOC-010（attribution identity chain，Claude 進行中）不同檔案面，
  rebase 注意 read_store.py 可能同時在動。

## 驗收

- [x] contract tests：apply→terminal→readback、restart 重放（模擬 store 重啟）、
      無 approval 409、containment 不可增資，全綠。
- [x] merge dev + dev 部署後 live curl 證明：create proposal → approve →
      apply → rebalance read `applied=true` → capital read 反映 target weight
      → 重啟後 proposal/receipt 仍可讀（babysit 規則：未經 live 驗證不得宣告完成）。
- [x] 證據歸檔 `docs/04/pantheon_persona_promotion_allocation_gap_2026-07-07/archive/`，
      並在 PPL-ALLOC-009 標記此 blocker 已清。

## Closeout evidence - 2026-07-13

- Final guarded-admission repair: PR #3536, merge commit
  `0e8c06603eb7ede8fd226837e439282e70fefc80`.
- Exact-SHA dev root deployment: run `29268814057`, success.
- Exact-SHA BFF restart: run `29270122636`, success; workflow log and public
  `/bff/version` both verified the exact merge SHA.
- Hosted governed apply: rebalance `rb-20260713-9e640fe8e883`, terminal command
  `cmd-29641b43c51241a0a4938a086ca3e180`, authoritative target weight `0.0101`,
  `live_capital_side_effects=false`.
- Guarded token `ct-ppl011-final-0e8c0660` read back `redeemed`; same-key replay
  preserved the command identity and a new-key reuse failed HTTP 428 before and
  after restart.
- Earlier apply and safe-containment evidence also survived both deployments;
  their pre-auto-redeem tokens are correctly inferred as consumed after the
  upgrade.
- Sanitized evidence:
  `docs/04/pantheon_persona_promotion_allocation_gap_2026-07-07/archive/PPL-ALLOC-011-HOSTED-EVIDENCE-2026-07-13.json`.

The accepted boundary is the current single-writer dev runtime and durable host
volume. This task does not claim multi-replica command-store coordination,
host-volume disaster recovery, or real broker/capital execution.
