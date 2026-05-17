# Broker Production Live 啟動 — Planning Session Brief

> 文件版本：v1.0 (kickoff brief, **not** consensus packet)
> 日期：2026-05-17
> 性質：discussion_planning session kickoff document (L2 planning artifact)
> 預期 session id：`phase8-2026-05-XX-broker-live-activation-criteria`
> 目標讀者：所有 AI lane（Codex/Codex2/Gemini/Gemini2/Claude/Claude2/Copilot）+ 人類 risk-owner + 人類 operator
> 動到的 L1 canonical：`PAPER_CANARY_LIVE_POLICY.md`、`KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`、`BINDING_AND_DEPLOYMENT_SEMANTICS.md`、`ROLLBACK_AND_POSITION_SEMANTICS.md`

⚠️ **本文件不是決議，是討論起點**。最終 consensus packet 必須過人類 risk-owner + operator 雙閘簽核才能 materialize 成 execution task。

---

## 0. 為什麼這需要 planning session 而非單一 task

Broker production live 啟動是一條「動真金」的單向行動，碰到 L1 政策、合規責任、跨 lane 專長。`AI_COLLABORATION_GUIDE.md` § 2.5 明文要求動到 L1 邊界的決策必須走 `discussion_planning` mode：每個 lane 寫獨立 readout、Codex 起 starter-draft、依 review_sequence 跨審、Claude 統稿、最後人類 human-gate 簽核。

單一 AI（包括我）不能自己拍板這件事。

## 1. Session Scope（討論範圍）

**In scope**：

1. 定義「broker production live 啟動」的**充分條件清單**（必要 evidence、必要 gate、必要 runbook）
2. 定義啟動後的**rollback drill 要求**（多久演練一次、誰負責、evidence 怎麼存）
3. 定義**人類雙閘簽核框架**（risk-owner 看什麼、operator 看什麼、簽核 ttl、撤回機制）
4. 定義**啟動後 first 7 days observation window** 的 telemetry 必看指標
5. 定義「啟動後仍要 fail-closed」的清單（kill-switch / safe-mode / 緊急停損條件）

**Out of scope**（不要在這個 session 討論）：

- 具體要不要啟動 — 那是業務決策，不是 AI session 能決定
- broker 選哪一家 — 已選 Shioaji，那是另個議題
- capital pool live binding — 雖然關聯，但是獨立 L1 政策，應另起 session
- BFF HA — 完全不相關，另起 session

## 2. 必須遵守的 fail-closed 鐵律

以下任一條被任何 AI lane 提議放寬，session **立即** 標 `human_required` 並停下：

1. 沒有 risk-owner + operator 雙人類簽核，**禁止** 任何啟動動作
2. 沒有 14 天連續 paper run + 7 天 canary run evidence chain，**禁止** 提報 live
3. OpenClaw 永遠不能作為 execution kernel，broker 連線必須走 services/broker/shioaji/ adapter
4. 啟動後 24 小時內必須有可執行的 kill-switch demo evidence
5. 任何 single point of failure（database、BFF、telemetry）有問題時必須立即 fail-closed
6. 短期 telemetry / 短期 alpha drift 不能直接觸發 live 變更，必須過 governance 至少 24 小時 cooldown

## 3. 議題清單（要解答的問題）

每個 lane 寫 readout 時要回答下列題目，引用具體 L1 文件、現有實作、或 evidence packet：

### Group A：Evidence 完整性（Codex / Codex2 主答）

A1. PromotionReadinessPacket schema 是否已能涵蓋 live 升級所需的所有 evidence kinds？缺什麼？
A2. M7-CANARY-CLOSEOUT task 產出的 packet 是否能作為 live 啟動的「上游」依據？欄位足夠嗎？
A3. 從 candidate → approved → paper → canary → live 的 lineage edge 要保留多久？哪個 store？
A4. 一筆 ApprovalDecision 從 proposed 到 decided 的 audit chain 要記哪些欄位才能事後可重播？

### Group B：Risk Surface（Gemini / Gemini2 主答）

B1. broker production live 連線的 secret / credential 怎麼存？rotate cadence？
B2. broker outage / API 變更 / rate limit 觸發時的 fail-closed semantics？
B3. live 期間 telemetry pipeline 斷線多久內必須觸發 kill-switch？
B4. 部署 live runtime 後的 infra cost ceiling 怎麼設？超過怎麼自動 safe-mode？
B5. canary → live 升級時要不要灰度（部分 capital 先上）？百分比怎麼定？

### Group C：Governance Loop（Claude / Claude2 主答）

C1. risk-owner 簽核應該看哪 N 份 evidence？最小集？建議集？
C2. operator 簽核要看哪些 N 份 evidence？跟 risk-owner 重疊嗎？
C3. 雙閘其中之一撤回時，live runtime 的處理 semantics？立即停？冷卻？
C4. live 啟動後 first 7 days 的 daily check-in 機制？誰主導？輸出什麼 evidence？
C5. live 過程中發生 incident，從 incident 觸發到 governance 停運的最大允許延遲？

### Group D：Compliance & Audit（Copilot 主答）

D1. 啟動 live 後的 audit log retention 要多久？哪些 field 必留？
D2. 跟監管 / 合規（如有）的回報義務？要不要事前告知？
D3. live → frozen / live → retired 的 transition 需要哪些 audit evidence？
D4. live 期間若發生 regulatory 詢問，evidence chain 能多快重組出來？

### Group E：Multi-Persona Sponsor（Claude 主答）

E1. 哪個 persona / sponsor 能對 live AllocationPolicyArtifact 負責？
E2. multi-persona 之間若有 conflict_resolution_log 矛盾，live 啟動前必須怎麼解？
E3. sponsor persona 退役 / suspend 時，live runtime 的處理？

## 4. 預期產出（Definition of Done for this Session）

session 結束時必須有：

1. **consensus-packet.md** — 上述 5 個 Group 共識答案
2. **broker_live_activation_criteria.json** — 機器可讀的啟動條件清單（給 OPS-BROKER-ACTIVATION-* task 用）
3. **risk_owner_checklist.md** + **operator_checklist.md** — 人類雙閘簽核 template
4. **rollback_drill_runbook.md** — 演練 SOP
5. **first_week_observation_window.md** — 7 天觀察期該看的 telemetry + decision tree
6. **fail_closed_invariants_appendix.md** — 啟動後仍維持的 fail-closed 清單（不可違反）

## 5. 建議的 Baton Sequence

```
1. Codex     starter-draft.md  (整合上述題目成統一框架)
2. Codex2    review-round-01 (從 schema / contract 角度補強)
3. Gemini    review-round-02 (從 infra / SLA / cost 角度補強)
4. Gemini2   review-round-03 (從 deploy / CI / rollback 角度補強)
5. Copilot   review-round-04 (從 compliance / critique 角度補強)
6. Claude    review-round-05 (從 governance / control plane 角度補強)
7. Claude2   review-round-06 (從 execution / kill-switch 角度補強)
8. Claude    consensus-packet.md (統稿)
9. document_reconciliation: 檢查 L1 (PAPER_CANARY_LIVE_POLICY etc) 是否需要 amend
10. human_gate: risk-owner + operator 簽核
11. materialize: 把共識切成 OPS-BROKER-ACTIVATION-* execution tasks
```

預估 8–12 個 wave 跑完（每個 round 1 wave，包含 readout + cross-review）。

## 6. 預期 materialize 出的 execution tasks（事後規劃，不在此階段派工）

僅作為「session 收尾後可能會派出的 task」預估，**不在 brief 階段執行**：

- OPS-BROKER-ACTIVATION-001 broker credential vault 與 rotation
- OPS-BROKER-ACTIVATION-002 live 啟動 staged 灰度策略
- OPS-BROKER-ACTIVATION-003 kill-switch fast path 演練 evidence
- OPS-BROKER-ACTIVATION-004 risk-owner / operator 雙閘 evidence collector
- OPS-BROKER-ACTIVATION-005 first 7 days observation window report builder
- OPS-BROKER-ACTIVATION-006 live → frozen / retired transition runbook
- OPS-BROKER-ACTIVATION-007 audit retention + compliance log builder
- OPS-BROKER-ACTIVATION-008 multi-persona sponsor conflict resolution gate

## 7. 預期不會派出的 tasks（這條 brief 明確劃線）

- 任何「直接啟動 live broker」的 task — 政策不允許
- 任何 fail-closed 旁路或繞過的 task — 鐵律不可放寬
- 任何「捷徑啟動」的 task — 必須過 14 + 7 天 paper/canary 累積

## 8. 啟動本 session 的決定點

- [ ] 確認 session id：`phase8-2026-05-XX-broker-live-activation-criteria`
- [ ] 確認 facilitator（建議 Claude）+ starter_owner（建議 Codex）
- [ ] 確認 5 個 Group 的主答 lane 分配
- [ ] 確認預估 wave 數（建議 8–12 個）
- [ ] 確認 human gate 簽核人（risk-owner + operator）的角色填入
- [ ] 確認 L1 amend 程序：若 session 結論需要修改 L1，怎麼走？

session 啟動指令（待你授權）：

```
./scripts/ai-status.sh planning open phase8-2026-05-XX-broker-live-activation-criteria
```

或由 chair-review 在下一個 wave-open 時自動觸發。
