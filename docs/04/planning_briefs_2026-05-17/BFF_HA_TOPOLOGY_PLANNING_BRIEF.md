# BFF HA / LB Production Topology — Planning Session Brief

> 文件版本：v1.0 (kickoff brief, **not** consensus packet)
> 日期：2026-05-17
> 性質：discussion_planning session kickoff document (L2 planning artifact)
> 預期 session id：`phase8-2026-05-XX-bff-ha-topology-poc`
> 目標讀者：所有 AI lane + 人類 infrastructure decision-maker
> 動到的 L1 canonical：`BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`、`TARGET_ARCHITECTURE.md`、`DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md`、`EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md`

⚠️ **本文件不是決議，是討論起點**。最終 consensus packet 必須過人類 infra decision-maker 簽核才能 materialize 成 PoC + execution task。

---

## 0. 為什麼這需要 planning session 而非單一 task

`BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` 是 L1 canonical，動到它就觸發 `AI_COLLABORATION_GUIDE.md` § 2.5 的 multi-AI consensus 要求。BFF HA 牽涉 GCP infra（Gemini lane 主場）、cost ceiling、SLA target、failover semantics、observability stack、database ownership policy 與 event ordering guarantees。單一 AI 不能把這些跨領域 trade-off 都壓在一個 task 裡。

額外風險：BFF 是 Management Console 與所有 lane 看到的「真相窗口」，做錯會讓整個 platform 看起來掛掉但實際好的（或反之）。需要保守、可演練、可 rollback 的設計，必須 cross-review。

## 1. Session Scope

**In scope**：

1. 定義 BFF HA 的**目標 SLA**（uptime、p99 latency、active connections）
2. 定義 BFF **拓樸**：單一 instance vs N replicas、是否要 LB、是否要 sticky session、SSE 怎麼 fanout
3. 定義 **degraded mode** 邊界：上游 service 掛了時 BFF 應回什麼、UI 看到什麼
4. 定義 **failover semantics**：active-passive vs active-active、主從切換 RTO/RPO
5. 定義 **observability stack** 必需指標（metrics、traces、logs、alerts）
6. 定義 **cost ceiling**：dev / staging / production 各環境月成本上限
7. 定義 **PoC 範圍**：先做哪一塊？驗證什麼假設？stop loss 條件？

**Out of scope**：

- BFF 程式碼重寫 — 不是這個 session 該決定
- 切到 service mesh / istio / consul — 過於 ambitious，需另起 session
- Database HA — 已由 `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` 涵蓋
- 切換雲端供應商 — 完全 out of scope

## 2. 必須遵守的 fail-closed 鐵律

1. BFF 永遠不能成為 canonical truth owner — 它只是 facade，degraded 時應該 typed-error 而非 invent state
2. SSE 連線斷線時 UI 必須能 detect 並降級展示（不能假裝資料還在流）
3. `strict` mode 任何上游 unavailable → 應該 typed error，**不可** silent fallback to fixture / seed
4. Multi-replica 部署時，每個 replica 看到的 ApprovalDecision 狀態必須一致 — 不允許「同個 approval 在兩 replica 出現兩個答案」
5. command path（`/bff/v1/commands`）的 idempotency 在 multi-replica 環境下必須仍生效，不允許重複執行

## 3. 議題清單（要解答的問題）

### Group A：SLA & Topology（Gemini / Gemini2 主答）

A1. 當前 BFF 在 dev 環境用什麼跑？production 預期跑在哪（GCP Cloud Run、GKE、Compute Engine）？
A2. p99 latency 目標：500 ms 過嗎？SSE 連線同時數目目標：100 / 1000 / 10000？
A3. LB 用 GCP HTTP(S) LB 還是 Cloud Run native？sticky session 對 SSE 必要嗎？
A4. dev / staging / production 三套各幾 replica？水平擴展觸發條件？
A5. 哪些 BFF route 必須 active-active（讀），哪些必須 active-passive（寫 / command）？

### Group B：Degraded Mode（Claude / Claude2 主答）

B1. 當 governance service unavailable，BFF `/bff/approvals` 應回什麼？404、503、cached snapshot、typed error？
B2. 當 telemetry service unavailable，`/bff/alerts` / `/bff/incidents` 應回什麼？
B3. SSE 連線重連時，replay 機制怎麼工作？要不要 last-event-id？多久後算 stale？
B4. `degraded` 狀態怎麼讓 UI 知道並顯示（HTTP header、JSON envelope、SSE event）？
B5. 上游 100% recovery 後 BFF 怎麼自動回 strict / live mode？要不要人類確認？

### Group C：Failover Semantics（Claude / Codex 主答）

C1. RTO（recovery time objective）目標多少？60 秒 / 300 秒 / 30 分鐘？
C2. RPO（recovery point objective）目標多少？資料遺失允許多少秒？
C3. failover 時 in-flight command 的處理：drop / replay / 人類解？
C4. failover 過程中 SSE 連線會斷一次，UI 該怎麼處理？
C5. 跨 replica 一致性：用 Redis 還是 Cloud Memorystore 還是另套？

### Group D：Observability（Gemini / Gemini2 主答）

D1. 必看的 BFF metric（route p99 latency、SSE active connections、command rejection rate、source meta degraded ratio）？
D2. trace propagation 用什麼（OpenTelemetry、Cloud Trace、Datadog）？
D3. log aggregation 怎麼做（Cloud Logging、ELK、Loki）？保留多久？
D4. alert threshold：p99 latency 超 X 秒、error rate 超 Y%、SSE 斷線 spike 等
D5. dashboard 給誰看？operator / risk-owner / 開發者 各自要看的不同嗎？

### Group E：Cost Ceiling（Gemini / Copilot 主答）

E1. dev 月成本 ceiling: 50 USD / 100 USD / 200 USD？哪個合理？
E2. staging 月成本 ceiling？
E3. production 月成本 ceiling？（含 LB、replica、Redis、observability）
E4. 超過 ceiling 自動降級 vs 自動 alarm？哪個策略？
E5. burst / scale-up 上限：最多幾 replica？防 runaway 成本？

### Group F：PoC 範圍（Claude / Claude2 + Gemini 主答）

F1. PoC 先做哪個 group 的 hypothesis？建議優先序？
F2. PoC stop loss：哪些 metric 失敗就停 PoC、回 baseline？
F3. PoC 完成後的 acceptance criteria：什麼成績可以進 production？
F4. PoC 與 production 之間是否要插一個 staging soak test？多久？
F5. PoC 過程中如何不影響當前 dev BFF 正常工作？

## 4. 預期產出

session 結束時必須有：

1. **consensus-packet.md** — 6 個 Group 共識答案
2. **bff_ha_topology.md** — 拓樸圖（mermaid）+ 各組件職責
3. **sla_targets.json** — 機器可讀 SLA target（給 alert / dashboard 用）
4. **degraded_mode_matrix.md** — 每個上游 service 掛時 BFF 該回什麼的對照表
5. **failover_runbook.md** — failover 演練 SOP
6. **observability_spec.md** — metrics / traces / logs / alerts 完整清單
7. **cost_ceiling.json** — 三套環境的 cost ceiling 與超額處理
8. **poc_scope_v0.md** — 第一版 PoC 的目標 / acceptance / stop loss

## 5. 建議的 Baton Sequence

```
1. Codex     starter-draft.md  (整合 6 個 Group 成統一框架，包含 mermaid 拓樸圖佔位)
2. Gemini    review-round-01 (SLA、topology、observability、cost — 主場)
3. Gemini2   review-round-02 (deploy / CI / multi-replica / scale-up 細節)
4. Codex2    review-round-03 (schema、contract、idempotency、event ordering 角度)
5. Copilot   review-round-04 (critique、cost vs SLA trade-off、alternatives)
6. Claude    review-round-05 (control plane、degraded mode、governance)
7. Claude2   review-round-06 (execution、command path、kill-switch 對 HA 的相依)
8. Claude    consensus-packet.md (統稿，含拓樸圖最終版)
9. document_reconciliation: 檢查 L1 (BFF_HA_AND_CONTROL_PLANE_RESILIENCE etc) 是否需要 amend
10. human_gate: infra decision-maker 簽核 PoC 範圍與 cost ceiling
11. materialize: 把 PoC v0 切成 OPS-BFF-HA-POC-* execution tasks
```

預估 6–10 個 wave 跑完。

## 6. 預期 materialize 出的 PoC v0 execution tasks（事後規劃）

僅作為 session 收尾後**可能**會派出的 PoC task 預估，**不在 brief 階段執行**：

- OPS-BFF-HA-POC-001 multi-replica BFF deployment skeleton（dev only）
- OPS-BFF-HA-POC-002 sticky session vs stateless SSE 比較測試
- OPS-BFF-HA-POC-003 degraded mode response shape unit tests
- OPS-BFF-HA-POC-004 active-passive failover demo（manual trigger）
- OPS-BFF-HA-POC-005 observability stack PoC（metrics + traces + alerts）
- OPS-BFF-HA-POC-006 cost ceiling tracking + auto-alarm
- OPS-BFF-HA-POC-007 idempotency under multi-replica integration test
- OPS-BFF-HA-POC-008 PoC retrospective + production go/no-go

PoC 通過 → 切 OPS-BFF-HA-PROD-* 後續 task；不通過 → 回 baseline + 列出 lessons learned。

## 7. 預期不會派出的 tasks（這條 brief 明確劃線）

- 任何直接上 production 多 replica 的 task — PoC 必須先過
- 任何切供應商的 task — out of scope
- 任何重寫 BFF 業務邏輯的 task — 只動部署拓樸，不動 business logic
- 任何放寬 fail-closed 的 task — 鐵律不可放寬

## 8. 啟動本 session 的決定點

- [ ] 確認 session id：`phase8-2026-05-XX-bff-ha-topology-poc`
- [ ] 確認 facilitator（建議 Claude）+ starter_owner（建議 Codex）
- [ ] 確認 6 個 Group 的主答 lane 分配（Gemini 是 SLA/infra/cost 主場）
- [ ] 確認預估 wave 數（建議 6–10）
- [ ] 確認 human gate 簽核人（infra decision-maker）
- [ ] 確認 cost ceiling 三套粗略額度
- [ ] 確認 PoC 在哪個環境跑（dev 影子環境 vs 一個 throwaway namespace）

session 啟動指令（待你授權）：

```
./scripts/ai-status.sh planning open phase8-2026-05-XX-bff-ha-topology-poc
```

或由 chair-review 在下一個 wave-open 時自動觸發。

## 9. 跟 Broker Live planning session 的關係

這兩個 session 雖然都是 L1-touching，但**互相獨立**：

- broker live 動的是「投資管理面」（PAPER_CANARY_LIVE_POLICY）
- BFF HA 動的是「平台 reliability 面」（BFF_HA_AND_CONTROL_PLANE_RESILIENCE）
- 兩者可以平行跑、不需要等對方
- 唯一交集：BFF HA degraded mode 必須能在 broker live 期間正確 fail-closed（不能因為 BFF degraded 就讓 live runtime 失去 governance 視野）— 這條約束兩 session 都要遵守
