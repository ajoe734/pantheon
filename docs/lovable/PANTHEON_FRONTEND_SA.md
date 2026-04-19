# Pantheon 前端 Master SA（給 Lovable 的完整系統框架）

> 文件目的：給 Lovable 的一站式前端系統分析與實作框架。  
> 文件角色：前端交付用 Master SA + 頁面級實作指引。  
> 版本：2026-04-19  
> 權威層級：implementation guide；不得覆寫 canonical blueprint、`WORKBENCH_DELIVERY_BACKLOG.md`、packet family、BFF contract。  
> 適用對象：Lovable 與任何前端實作 lane。  

---

## 1. 這份文件怎麼用

### 1.1 這份文件要解決的問題

Pantheon 前端不能再用「一頁一頁接 spec」的方式交付。

Lovable 需要先知道：

- Pantheon 整體是什麼產品
- 八大 workbench 怎麼組成一個完整系統
- 哪些頁面已可正式實作
- 哪些頁面只能做 overview shell
- 哪些頁面仍 blocked，不得發明資料或互動
- 共用哪些殼層、layout、drawer、realtime、degradation primitive

### 1.2 與其他文件的關係

這份文件是 Lovable 的交付入口，但不是最終 canonical truth。

若有衝突，權威順序如下：

1. `TARGET_ARCHITECTURE.md`
2. `ROADMAP.md`
3. `DEVELOPMENT_WORKBREAKDOWN.md`
4. `WORKBENCH_DELIVERY_BACKLOG.md`
5. 對應的 `PACKET_FAMILY.md`
6. 對應的 `docs/bff/*.md`
7. 對應的 `docs/screens/*.md`
8. 對應的 `FRONTEND_CHANGE_SPEC.md`
9. 本文件

### 1.3 Lovable 的閱讀順序

1. 先讀本文件，理解整體系統框架與頁面地圖
2. 確認目標模組在 `WORKBENCH_DELIVERY_BACKLOG.md` 的 readiness
3. 讀對應 packet family
4. 讀對應 screen spec
5. 讀對應 BFF contract
6. 讀對應 `FRONTEND_CHANGE_SPEC.md`
7. 再開始做畫面

### 1.4 Readiness 標記

本文件所有頁面都會明確標示以下其中一種：

- `ready`：已有穩定 packet 與 BFF，可正式做 production UI
- `contract-ready`：contract 與 screen spec 已就位，可做 production UI
- `overview-only`：只能做總覽頁或 shell，不可發明模組內頁
- `blocked`：不可做 production UI；最多只能做明確標記的 non-production shell / IA wireframe

---

## 2. Pantheon 是什麼產品

Pantheon 不是一組彼此無關的 dashboard。

它是同一個 governed operating system，處理：

1. operator live monitoring
2. governance review / approval / rollback
3. persona / capital / binding management
4. incident response and post-incident evidence
5. evolution review and lineage
6. research workflow
7. knowledge and evidence browsing
8. consultation and trainer workflows

前端必須讓使用者感覺自己在同一個系統裡移動，而不是在 20 個獨立頁面之間跳來跳去。

---

## 3. 八大 Workbench 總覽

```
Pantheon
├── Operator Console
├── Governance Workbench
├── Evolution Workbench
├── Persona Workbench
├── Research Workbench
├── Knowledge Workbench
├── Consultation Workbench
└── Trainer Workbench
```

### 3.1 每個 workbench 的角色

| Workbench | 角色 | 現況 |
|---|---|---|
| `Operator Console` | live monitoring, health, incidents, runtime, drift | ready / contract-ready |
| `Governance` | review, approval, diff, rollback, audit, promotion | ready / contract-ready |
| `Evolution` | post-incident, evolution decision, lineage, future mutation review | partial |
| `Persona` | persona, sessions, teaching, capabilities, capital, bindings | ready |
| `Research` | tickets, search, analysis, experiments, artifact compare | blocked |
| `Knowledge` | memory, notes, evidence, insight cards, strategy specs | KW-01 contract-ready; KW-02–05 blocked |
| `Consultation` | request, transcript, committee board, red-team memo | overview-only + blocked modules |
| `Trainer` | teaching dialog, controls, compare, replay | TW-01 contract-published; TW-02–04 blocked |

---

## 4. 全域殼層與導航

### 4.1 App Shell

所有 workbench 都應共用同一個 shell：

- 左側主導航 rail：八大 workbench
- 頂部 top bar：環境、freshness、alerts entry、quick search
- 內容區最上方：Global Degradation Banner
- 主要內容區：當前 workbench 頁面
- 右側次要面板：detail drawer / evidence drawer / inspector

### 4.2 建議主導航

| Sidebar section | 內容 |
|---|---|
| `Overview` | Operator Home 作為整體入口 |
| `Operator Console` | alerts, health, runtime, incidents, drift |
| `Governance` | deployment review, review queue, approval queue, diff, rollback, audit, promotion |
| `Evolution` | evolution center, lineage, inspiration, mutation review |
| `Personas` | personas, sessions, teaching, capabilities, pools, bindings, drilldowns |
| `Research` | overview + blocked module shell |
| `Knowledge` | overview + blocked module shell |
| `Consultation` | overview + blocked module shell |
| `Trainer` | blocked shell |

### 4.3 路由分層規則

這份文件中的 path 分兩種：

- `實作路徑`：已有 packet / contract 支撐，可做 production UI
- `建議 shell 路徑`：為了讓整體 IA 完整而定義的前端 path；若模組未 ready，只能做 shell，不得發明資料或互動

---

## 5. 全域實作規則

### 5.1 BFF-first only

所有頁面都必須渲染 backend-shaped read model。

禁止：

- 在前端 join 多條路由重建業務資料
- 在前端計算 health、diff、drift、governance outcome、committee verdict
- 自行推斷 lifecycle、risk、approval authority、mutation authority

### 5.2 CTA authority 規則

所有寫入 CTA 都必須來自：

- `allowedActions`
- 或 packet 明確定義的 write contract

如果 authority signal 不存在，就不顯示 CTA。

### 5.3 Mutation path 規則

目前 `Operator Console` 與 `Governance` 的 mutation 主要走：

- `POST /api/v1/operator/commands`

但這不是全產品永遠唯一的寫入模式。  
若後續 `Research`、`Consultation`、`Trainer` 的 packet family 定義 module-local write route，必須依 packet contract 為準。

### 5.4 四種必要狀態

每個資料面板都必須有：

- `loading`
- `empty`
- `degraded`
- `unavailable`
- `error`

禁止把 degraded 當成 empty。

### 5.5 `meta.surfaces` 規則

`meta.surfaces.*` 是前端判斷面板健康的唯一 truth。

- `ok`：正常顯示資料
- `degraded`：顯示資料，但必須有 degradation copy
- `unavailable`：替換整個面板，不顯示假資料
- `stale` / `served_from cache`：顯示 freshness / stale copy

### 5.6 Global Degradation Banner

所有頁面頂部都要預留 banner 區。

Banner 只讀當前頁面 response 的 `meta.surfaces` 與 staleness，不得額外 ping 健康端點。

### 5.7 SSE 規則

SSE 是 cross-cutting substrate，不是資料真相來源。

- 先用 BFF composed read model render 首屏
- SSE 只更新已存在的 surface
- 只有 packet family 或 `PKT-005` live-update semantics 有要求的頁面才需要接 SSE
- 不可把頁面做成「沒有 SSE 就看不懂」

### 5.8 共用前端 primitive

Lovable 應先做以下 primitive，再做頁面：

| Primitive | 用途 |
|---|---|
| `GlobalDegradationBanner` | 所有頁面共用 |
| `SurfaceStateRenderer` | loading / empty / degraded / unavailable / error |
| `QueueShell` | review queue, approval queue, alerts, audit |
| `BoardShell` | operator home, health, runtime, committee board |
| `CompareShell` | deployment diff, drift, artifact compare, before/after compare |
| `GraphShell` | lineage, inspiration |
| `EvidenceDrawer` | governance, evolution, knowledge, consultation, trainer |
| `CommandRail` | 只有真正擁有 write authority 的頁面可用 |
| `RealtimeFooterRail` | SSE 連線狀態、delay note、reconnect 狀態 |

---

## 6. 全域路由地圖

### 6.1 已可正式實作的頁面

| Path | 頁面 | Workbench | 狀態 |
|---|---|---|---|
| `/` | Operator Home Dashboard | Operator Console | contract-ready |
| `/operator/alerts` | Alerts Rail | Operator Console | contract-ready |
| `/operator/health` | Health Status Board | Operator Console | contract-ready |
| `/operator/runtime` | Runtime State Board | Operator Console | contract-ready |
| `/operator/drift/:runtime_id` | Paper / Live Drift | Operator Console | contract-ready |
| `/operator/incidents` | Incident Home | Operator Console | ready |
| `/operator/incidents/:incident_id` | Incident Detail | Operator Console | ready |
| `/operator/incidents/:incident_id/review` | Post-Incident Review | Evolution baseline | ready |
| `/governance/deployment` | Deployment Review Console | Governance | ready |
| `/governance/review-queue` | Governance Review Queue | Governance | ready |
| `/governance/approval-queue` | Governance Approval Queue | Governance | contract-ready |
| `/governance/diff/:plan_id` | Deployment Diff | Governance | contract-ready |
| `/governance/rollback/:rollback_id` | Rollback Review | Governance | ready |
| `/governance/audit` | Governance Audit Rail | Governance | ready |
| `/governance/promotion/:artifact_id` | Promotion Review | Governance | ready |
| `/evolution` | Evolution Center | Evolution | ready |
| `/evolution/lineage` | Lineage View | Evolution | ready |
| `/personas` | Persona Catalog | Persona | ready |
| `/personas/:persona_id` | Persona Detail | Persona | ready |
| `/personas/:persona_id/manage` | Persona Management | Persona | ready |
| `/personas/:persona_id/sessions` | Persona Sessions | Persona | ready |
| `/sessions/:session_id` | Session Detail | Persona | ready |
| `/personas/:persona_id/teaching` | Teaching History | Persona | ready |
| `/personas/:persona_id/capabilities` | Capability Snapshot | Persona | ready |
| `/capital-pools` | Capital Pool List | Persona | ready |
| `/capital-pools/:pool_id` | Capital Pool Detail | Persona | ready |
| `/bindings` | Binding List | Persona | ready |
| `/bindings/:binding_id` | Binding Detail | Persona | ready |
| `/deployment-plans` | Deployment Plan List | Persona drilldown | ready |
| `/deployment-plans/:plan_id` | Deployment Plan Detail | Persona drilldown | ready |
| `/approval-decisions` | Approval Decision List | Persona drilldown | ready |
| `/approval-decisions/:decision_id` | Approval Decision Detail | Persona drilldown | ready |
| `/research` | Research Overview | Research | shell-only overview |
| `/knowledge` | Knowledge Overview | Knowledge | overview-only |
| `/consultation` | Consultation Overview | Consultation | overview-only |

### 6.2 建議 shell 路徑（未 ready 模組）

這些 path 用來保證整體 IA 完整，但只有在該模組 `ready` 後才能做 production UI。

| Path | 頁面 | Workbench | 目前可做的程度 |
|---|---|---|---|
| `/evolution/inspiration/:artifact_id` | Inspiration Graph | Evolution | contract published — add "coming soon / blocked by Pantheon BFF" placeholder; production page pending BFF route |
| `/evolution/mutation-review/:decision_id` | Mutation Review | Evolution | contract published — add "coming soon / blocked by Pantheon BFF" placeholder; production page pending BFF route and command vocabulary |
| `/research/tickets` | Research Ticket List | Research | contract published — add "coming soon / blocked by Pantheon BFF" placeholder; production page pending BFF routes |
| `/research/tickets/:ticket_id` | Research Ticket Detail | Research | contract published — add "coming soon / blocked by Pantheon BFF" placeholder; production page pending BFF routes |
| `/research/search` | Search | Research | contract published — add "coming soon / blocked by Pantheon BFF" placeholder; production page pending BFF route and search index adapter |
| `/research/analyze` | Analyze | Research | contract published — add "coming soon / blocked by Pantheon BFF" placeholder; production page pending BFF routes |
| `/research/experiments` | Experiment Launch / Run History | Research | blocked shell only |
| `/research/compare` | Artifact Compare | Research | blocked shell only |
| `/knowledge/memory` | Institutional Memory | Knowledge | contract-ready |
| `/knowledge/memory/:entry_id` | Memory Detail | Knowledge | contract-ready |
| `/knowledge/notes` | Research Notes | Knowledge | blocked shell only |
| `/knowledge/notes/:note_id` | Note Detail | Knowledge | blocked shell only |
| `/knowledge/evidence` | Evidence Refs | Knowledge | blocked shell only |
| `/knowledge/evidence/:ref_id` | Evidence Ref Detail | Knowledge | blocked shell only |
| `/knowledge/insights` | Insight Cards | Knowledge | blocked shell only |
| `/knowledge/insights/:insight_id` | Insight Card Detail | Knowledge | blocked shell only |
| `/knowledge/strategy-specs` | Strategy Spec List | Knowledge | blocked shell only |
| `/knowledge/strategy-specs/:strategy_id` | Strategy Spec Detail | Knowledge | blocked shell only |
| `/knowledge/strategy-specs/:strategy_id/compare` | Strategy Spec Compare | Knowledge | blocked shell only |
| `/consultation/requests` | Consult Request | Consultation | blocked shell only |
| `/consultation/requests/:request_id` | Consult Request Detail | Consultation | blocked shell only |
| `/consultation/transcripts/:session_id` | Debate Transcript | Consultation | blocked shell only |
| `/consultation/committees` | Committee Board | Consultation | blocked shell only |
| `/consultation/committees/:committee_id` | Committee Detail | Consultation | blocked shell only |
| `/consultation/memos` | Red-team Memo List | Consultation | blocked shell only |
| `/consultation/memos/:memo_id` | Red-team Memo Detail | Consultation | blocked shell only |
| `/trainer` | Trainer Landing | Trainer | blocked shell only |
| `/trainer/sessions` | Teaching Dialog / Session List | Trainer | pending-bff placeholder only |
| `/trainer/sessions/:session_id` | Teaching Dialog Detail | Trainer | pending-bff placeholder only |
| `/trainer/sessions/:session_id/controls` | Parameter Controls | Trainer | blocked shell only |
| `/trainer/sessions/:session_id/compare` | Before/After Compare | Trainer | blocked shell only |
| `/trainer/replay` | Teaching Replay List | Trainer | blocked shell only |
| `/trainer/replay/:session_id` | Teaching Replay Detail | Trainer | blocked shell only |

---

## 7. Operator Console

### 7.1 使命

讓 operator 知道現在系統發生什麼、哪裡不健康、哪裡需要立刻介入，並導向正確 owner surface。

### 7.2 頁面清單

| 頁面 | Path | API / packet | 狀態 | 建議呈現 |
|---|---|---|---|---|
| Operator Home | `/` | `GET /api/v1/operator/home`, `PKT-013` | contract-ready | dashboard + shortcut rail |
| Alerts Rail | `/operator/alerts` | `GET /api/v1/operator/alerts`, `PKT-012` | contract-ready | chronological alert rail |
| Health Status | `/operator/health` | `GET /api/v1/operator/health-status`, `PKT-011` | contract-ready | grouped health board |
| Runtime State | `/operator/runtime` | `GET /api/v1/operator/runtime-state`, `PKT-010` | contract-ready | dense operational board |
| Paper / Live Drift | `/operator/drift/:runtime_id` | `GET /api/v1/operator/paper-live-drift/{runtime_id}`, `PKT-014` | contract-ready | compare shell |
| Incident Home | `/operator/incidents` | `GET /api/v1/incidents`, `GET /api/v1/kill-switch/status`, `PKT-002` | ready | list + top rail |
| Incident Detail | `/operator/incidents/:incident_id` | `GET /api/v1/operator/incident-response/{incident_id}`, `PKT-002` | ready | detail page + action drawer |

### 7.3 頁面定義

#### 7.3.1 Operator Home

- 目的：單一入口總結 alerts、incidents、governance、runtime、health、safe mode
- 必須包含：summary header、backend-sorted cards、escalation shortcuts
- 不得做：任何 write CTA、重新呼叫其他端點自行組卡片

#### 7.3.2 Alerts Rail

- 目的：顯示 active alerts 的時間序列與 owner target
- 必須包含：summary、alert list、severity/category chips、target links
- 不得做：acknowledge / dismiss、前端 join incident/runtime 補資料

#### 7.3.3 Health Status

- 目的：呈現 runtime / telemetry / incident / governance / kill-switch 五組健康狀態
- 必須包含：summary、group cards、surface refs、secondary control path
- 不得做：把多個 raw route 自己拼成 health 狀態

#### 7.3.4 Runtime State

- 目的：看所有 runtime 的 stage、status、telemetry summary、rollback summary
- 必須包含：filter rail、runtime table、telemetry cell、rollback cell、pagination
- 不得做：逐列補抓 raw telemetry、加 rollback/pause/promotion CTA

#### 7.3.5 Paper / Live Drift

- 目的：看 paper 與 live 的 drift comparison
- 必須包含：comparison header、threshold summary、drift groups、evidence refs、recommended actions
- 不得做：自行從 raw telemetry / policy 算 drift

#### 7.3.6 Incident Home

- 目的：處理 active incidents 的入口
- 必須包含：kill switch top rail、open/resolved tabs、incident rows、SSE integration
- SSE：incident stream + kill-switch updates
- 不得做：靠 polling 取代 packet 要求的 live-update 語意

#### 7.3.7 Incident Detail

- 目的：檢視單一 incident 的 timeline、evidence、kill switch 狀態與 action authority
- 必須包含：summary、timeline、evidence、embedded kill-switch badge、action drawer trigger
- Action Drawer：
  - 讀 `GET /api/v1/kill-switch/status`
  - 寫 `POST /api/v1/operator/commands`
  - commands 受 `allowedActions` 與 PKT-002 contract 控制

---

## 8. Governance Workbench

### 8.1 使命

用 deliberate、evidence-backed 的方式處理 review、approval、diff、rollback、audit、promotion。

### 8.2 頁面清單

| 頁面 | Path | API / packet | 狀態 | 建議呈現 |
|---|---|---|---|---|
| Deployment Review Console | `/governance/deployment` | `PKT-001` | ready | list-detail review console |
| Governance Review Queue | `/governance/review-queue` | `PKT-001` | ready | queue shell + drawer |
| Approval Queue | `/governance/approval-queue` | `PKT-006` | contract-ready | queue shell + decision drawer |
| Deployment Diff | `/governance/diff/:plan_id` | `PKT-007` | contract-ready | structured compare page |
| Rollback Review | `/governance/rollback/:rollback_id` | `PKT-008` | ready | focused review surface |
| Governance Audit Rail | `/governance/audit` | `PKT-009` | ready | audit ledger |
| Promotion Review | `/governance/promotion/:artifact_id` | `F-042` | ready | focused detail review |

### 8.3 頁面定義

#### 8.3.1 Deployment Review Console

- 目的：review deployment plans，顯示 risk、bindings、runtime binding、latest run progress
- 必須包含：list panel、detail panel、authority-based CTA
- 可接 SSE：runtime event stream，僅作 overlay

#### 8.3.2 Governance Review Queue

- 目的：統一治理 review queue
- 必須包含：filter rail、row list、detail drawer、authority-based CTA
- 不得做：前端自己推斷誰能 approve / reject

#### 8.3.3 Approval Queue

- 目的：處理 pending approval decisions
- 必須包含：decision list、detail drawer、evidence refs、governance chain、required approvals
- CTA 只依 `allowedActions.canApprove / canReject / canRequestRevision`

#### 8.3.4 Deployment Diff

- 目的：呈現 plan 與 prior plan 的 backend-owned diff
- 必須包含：identity header、grouped diff summary、field diff table、approval gating panel
- 不得做：在前端算 diff、顯示未變欄位

#### 8.3.5 Rollback Review

- 目的：review rollback scope、position impact、bindings、trigger evidence
- 必須包含：scope summary、position impact table、trigger evidence、approval actions
- 特別規則：`position_data` degraded/unavailable 時，approve CTA 必須停用

#### 8.3.6 Governance Audit Rail

- 目的：看歷史治理行為與 evidence
- 必須包含：audit list、detail drawer、server-side filter rail
- 不得做：任何 mutation

#### 8.3.7 Promotion Review

- 目的：review artifact promotion to paper
- 必須包含：artifact identity、review summary、`canPromoteToPaper` authority、supporting evidence

---

## 9. Evolution Workbench

### 9.1 使命

連接 incident evidence、evolution decision、lineage、未來的 inspiration 與 mutation review。

### 9.2 頁面清單

| 頁面 | Path | API / packet | 狀態 | 建議呈現 |
|---|---|---|---|---|
| Post-Incident Review | `/operator/incidents/:incident_id/review` | `PKT-003` | ready | split-pane evidence review |
| Evolution Center | `/evolution` | `PKT-003` | ready | board or list-detail |
| Lineage View | `/evolution/lineage` | `PKT-003` | ready | graph + inspector |
| Inspiration Graph | `/evolution/inspiration/:artifact_id` | `EW-04` | contract-published | placeholder until BFF route live |
| Mutation Review | `/evolution/mutation-review/:decision_id` | `EW-05` | contract-published | placeholder until BFF route and command vocabulary live |

### 9.3 頁面定義

#### 9.3.1 Post-Incident Review

- 目的：看 resolved incident 的 postmortem、findings、action items、related incidents
- API：`GET /api/v1/operator/post-incident-review/{incident_id}` 等 packet 指定讀路由
- 呈現：header + postmortem panel + related incidents rail

#### 9.3.2 Evolution Center

- 目的：看 evolution decisions、freeze orders、rollbacks
- 必須包含：三面板並列、decision detail drawer、server-side filters
- 不得做：mutation CTA

#### 9.3.3 Lineage View

- 目的：看 artifact lineage 與 edge detail
- 必須包含：list panel、graph canvas、inspector drawer
- 不得做：把 graph 空狀態畫成空白畫布

#### 9.3.4 Inspiration Graph

- 目的：artifact-centered inspiration graph
- 契約狀態：route contract、composed object 與 `meta.surfaces.inspiration` 已透過 `EW-04-OPEN-001` 發布；BFF route `GET /api/v1/lineage/inspiration/{artifact_id}` 仍待實作
- 現在可做：placeholder 標記 `coming soon / blocked by Pantheon BFF`；待 BFF 確認 route 上線後即可進入正式實作
- 不可做：用 existing lineage routes 拼 inspiration graph；在 BFF route 上線前開 production page

#### 9.3.5 Mutation Review

- 目的：review mutation proposal 與 evidence
- 契約狀態：read route contract (`GET /api/v1/operator/mutation-review/{decision_id}`)、composed `MutationReviewProjection` object、`ApproveMutation` / `RejectMutation` command vocabulary、`allowedActions` authority signals 與 `meta.surfaces.mutation_review` staleness signal 已透過 `EW-05-OPEN-001` 發布；BFF route 與 operator command extension 仍待實作
- 現在可做：placeholder 標記 `coming soon / blocked by Pantheon BFF`；待 BFF 確認 route 與 command vocabulary 上線後即可進入正式實作
- 不可做：把現有 evolution decision 詳頁包一層按鈕就當 mutation review；在 BFF route 上線前開 production page

---

## 10. Persona Workbench

### 10.1 使命

提供 read-heavy 的 persona、session、capability、capital、binding、deployment context surface。

### 10.2 頁面清單

| 頁面 | Path | API / packet | 狀態 | 建議呈現 |
|---|---|---|---|---|
| Persona Catalog | `/personas` | `PKT-004`, `PS-01` | ready | registry list |
| Persona Detail | `/personas/:persona_id` | `PKT-004`, `PS-02` | ready | detail page |
| Persona Management | `/personas/:persona_id/manage` | `PKT-004`, `PM-01` | ready | composed workspace |
| Persona Sessions | `/personas/:persona_id/sessions` | `PS-03` | ready | list-detail |
| Session Detail | `/sessions/:session_id` | `PS-04` | ready | detail page |
| Teaching History | `/personas/:persona_id/teaching` | `PS-05` | ready | history list |
| Capability Snapshot | `/personas/:persona_id/capabilities` | `PS-06` | ready | snapshot viewer |
| Capital Pool List | `/capital-pools` | `CP-01` | ready | registry list |
| Capital Pool Detail | `/capital-pools/:pool_id` | `CP-02` | ready | detail + linked bindings |
| Binding List | `/bindings` | `CP-03` | ready | registry list |
| Binding Detail | `/bindings/:binding_id` | `CP-04` | ready | detail page |
| Deployment Plan List | `/deployment-plans` | `DP-01` | ready | read-only drilldown |
| Deployment Plan Detail | `/deployment-plans/:plan_id` | `DP-02` | ready | read-only drilldown |
| Approval Decision List | `/approval-decisions` | `DP-03` | ready | read-only drilldown |
| Approval Decision Detail | `/approval-decisions/:decision_id` | `DP-04` | ready | read-only drilldown |

### 10.3 頁面定義

#### 10.3.1 Persona Catalog / Detail

- Catalog：看 personas with lifecycle, mandate, strategy family
- Detail：看 persona identity、bindings summary、jump links to sessions / teaching / capabilities

#### 10.3.2 Persona Management

- 目的：單一 composed endpoint 呈現 persona summary、bindings、active sessions、teaching history、action rail
- API：`GET /api/v1/operator/persona-management/{persona_id}`
- 規則：
  - panel degradation 需要 truthfully 標示
  - CTA 只能來自 `allowedActions`
  - 不得從 persona state 推斷可做什麼

#### 10.3.3 Sessions / Teaching / Capabilities

- Sessions：讀 persona sessions list，row 進 session detail
- Teaching：讀 teaching session 歷史，不是 Trainer workflow 的替代品
- Capabilities：顯示 `effective_tools[]`, `effective_skills[]`, `effective_workflows[]`, restrictions

#### 10.3.4 Capital / Binding

- Capital Pool List / Detail：看 capital pool identity、policy、linked bindings
- Binding List / Detail：看 persona-capital bindings
- 規則：filter 一律 server-side；若 query param 行為與 contract 不符，emit BFF gap，不要前端過濾補救

#### 10.3.5 Deployment / Approval Drilldowns

- 角色：Persona workbench 內的 read-only context，不是 governance 主流程
- 必須有：`View in Governance Console`
- 不得有：Approve / Reject CTA

---

## 11. Research Workbench

### 11.1 使命

提供從 research ticket 到 search、analysis、experiment、artifact compare 的完整研究流。

### 11.2 Workbench 頁面清單

| 頁面 | Path | 期待 contract | 狀態 | 目前可做 |
|---|---|---|---|---|
| Research Overview | `/research` | overview shell | blocked family overview | 可做 overview |
| Research Ticket List | `/research/tickets` | `RW-01` | contract-published | pending-bff placeholder only |
| Research Ticket Detail | `/research/tickets/:ticket_id` | `RW-01` | contract-published | pending-bff placeholder only |
| Search | `/research/search` | `RW-02` | contract-published | pending-bff placeholder only |
| Analyze | `/research/analyze` | `RW-03` | contract-published | pending-bff placeholder only |
| Experiment Launch / History | `/research/experiments` | `RW-04` | blocked | shell-only |
| Artifact Compare | `/research/compare` | `RW-05` | blocked | shell-only |

### 11.3 頁面定義

#### 11.3.1 Research Overview

- 目的：說明 research workbench 模組地圖、依賴順序、blocked 原因
- 必須包含：五個模組卡片、每個模組缺哪些 BFF routes、何時可開工
- 不可偽裝成真實 ticket/search/experiment 系統

#### 11.3.2 RW-01 Research Ticket

- 目標頁面：list、detail、create/edit、lifecycle transition
- 期待 BFF：ticket list/detail/create/patch routes
- 生產版呈現：list-detail + right detail panel
- 契約已發佈，可先做明確 blocked placeholder；在 BFF routes 未落地前不得發明 ticket data model

#### 11.3.3 RW-02 Search

- 目標頁面：query bar、filter rail、result list、result drilldown
- 期待 BFF：backend-owned search route 和 index adapter
- 契約已發佈，可先做明確 blocked placeholder；在 BFF route 與 search index adapter 未落地前，不可做假 corpus、不可把別頁資料 client-side 搜尋

#### 11.3.4 RW-03 Analyze

- 目標頁面：analysis result view、metric groups、comparative summary
- 期待 BFF：analysis list/detail、backend-owned metric aggregation contract
- 契約狀態：BFF routes (`GET /api/v1/research/analysis`, `GET /api/v1/research/analysis/{analysis_id}`) 與 metric aggregation / comparative summary payload 已透過 `RW-03-ANALYZE-001` 發布。
- 現在可做：明確 pending-bff placeholder；等待 live BFF route 對齊 published contract。
- 不可做：從 raw result 自己 grouping，或在前端自行比對多個 analysis payload 生成 compare summary。

#### 11.3.5 RW-04 Experiment Launch

- 目標頁面：launch form、async status、run history、run detail
- 期待 BFF：launch route、experiment state machine、status route、cancel authority
- 在 route 未落地前：不可做假進度條或假 async state machine

#### 11.3.6 RW-05 Artifact Compare

- 目標頁面：artifact selector、structured diff、side-by-side compare、evidence drawer
- 期待 BFF：artifact registry、artifact detail、compare route、versioning semantics
- 在 route 未落地前：不可在前端比較兩份 JSON 假裝是產品 diff

---

## 12. Knowledge Workbench

### 12.1 使命

提供 institutional memory、notes、evidence、insight cards、strategy spec 的 durable browsing surface。

### 12.2 頁面清單

| 頁面 | Path | 期待 contract | 狀態 | 目前可做 |
|---|---|---|---|---|
| Knowledge Overview | `/knowledge` | `PKT-knowledge-workbench` | overview-only | 可正式做 |
| Institutional Memory List | `/knowledge/memory` | `KW-01` | contract-ready | 待 BFF 上線後可正式做 |
| Institutional Memory Detail | `/knowledge/memory/:entry_id` | `KW-01` | contract-ready | 待 BFF 上線後可正式做 |
| Research Notes List | `/knowledge/notes` | `KW-02` | blocked | shell-only |
| Research Note Detail | `/knowledge/notes/:note_id` | `KW-02` | blocked | shell-only |
| Evidence Refs List | `/knowledge/evidence` | `KW-03` | blocked | shell-only |
| Evidence Ref Detail | `/knowledge/evidence/:ref_id` | `KW-03` | blocked | shell-only |
| Insight Cards | `/knowledge/insights` | `KW-04` | blocked | shell-only |
| Insight Card Detail | `/knowledge/insights/:insight_id` | `KW-04` | blocked | shell-only |
| Strategy Spec List | `/knowledge/strategy-specs` | `KW-05` | blocked | shell-only |
| Strategy Spec Detail / Compare | `/knowledge/strategy-specs/:strategy_id`, `/compare` | `KW-05` | blocked | shell-only |

### 12.3 頁面定義

#### 12.3.1 Knowledge Overview

- API：`GET /api/v1/workbench/knowledge`
- 必須包含：header、module sequence、missing contracts、next steps
- 這頁是 overview shell，不代表 KW-01~05 已可開工

#### 12.3.2 KW-01 Institutional Memory

- 目標頁面：memory entry list + detail
- 生產版呈現：library-like list-detail
- 契約狀態：BFF routes (`GET /api/v1/knowledge/memory`, `GET /api/v1/knowledge/memory/{entry_id}`) 與 browse projection 已透過 `KW-01-FOUNDATION-001` 發布。
- 現在可做：正式 production UI (需先確認 BFF 實作完成，或使用 `KW-01` example payload)。
- 不可做：invent browse projection；在 BFF route 實作完成前交付正式版。

#### 12.3.3 KW-02 Research Notes

- 目標頁面：notes list/detail、ownership、attachment target
- 期待 BFF：note create/list/detail + ownership/attachment contract
- shell 階段：不可自己定 attachment taxonomy

#### 12.3.4 KW-03 Evidence Refs

- 目標頁面：evidence registry + detail + linked decision panel
- 期待 BFF：evidence list/detail + resolved links
- shell 階段：不可從 raw id 猜 URL

#### 12.3.5 KW-04 Insight Cards

- 目標頁面：card grid、card detail、filter rail、linked-source drilldown
- 期待 BFF：aggregation endpoint、card detail endpoint、filter taxonomy
- shell 階段：不可 client-side 聚合 notes + evidence + memory

#### 12.3.6 KW-05 Strategy Spec

- 目標頁面：spec list、viewer、citation panel、version compare
- 期待 BFF：list/detail/versioning/diff contract
- shell 階段：不可比較 raw spec JSON 假裝是正式 compare

---

## 13. Consultation Workbench

### 13.1 使命

提供 consult request、ordered transcript、committee board、red-team memo 的 structured deliberation system。

### 13.2 頁面清單

| 頁面 | Path | 期待 contract | 狀態 | 目前可做 |
|---|---|---|---|---|
| Consultation Overview | `/consultation` | `PKT-consultation-workbench` | overview-only | 可正式做 |
| Consult Request List / Composer | `/consultation/requests` | `CW-01` | contract-published | pending-bff placeholder only |
| Consult Request Detail | `/consultation/requests/:request_id` | `CW-01` | contract-published | pending-bff placeholder only |
| Debate Transcript | `/consultation/transcripts/:session_id` | `CW-02` | blocked | shell-only |
| Committee Board | `/consultation/committees` | `CW-03` | blocked | shell-only |
| Committee Detail | `/consultation/committees/:committee_id` | `CW-03` | blocked | shell-only |
| Red-team Memo List | `/consultation/memos` | `CW-04` | blocked | shell-only |
| Red-team Memo Detail | `/consultation/memos/:memo_id` | `CW-04` | blocked | shell-only |

### 13.3 頁面定義

#### 13.3.1 Consultation Overview

- API：`GET /api/v1/workbench/consultation`
- 必須包含：overview status、module sequence、missing contracts、next steps
- 不可把 overview page 做成假 request list

#### 13.3.2 CW-01 Consult Request

- 目標頁面：request composer、request list、request detail、cancel path、request-to-session status
- 已發布 contract：`docs/bff/CW-01-consult-request.md`、`docs/screens/CW-01-consult-request.md`、`docs/examples/CW-01-consult-request.json`
- 前端 handoff：`docs/pantheon-handoffs/CW-01-consult-request/FRONTEND_CHANGE_SPEC.md`
- 目前 gate：BFF 必須先讓 create/list/detail/cancel routes 上線並回傳已發布 field shape

#### 13.3.3 CW-02 Debate Transcript

- 目標頁面：ordered transcript、actor badges、inline evidence、replay
- 期待 BFF：append-only `TranscriptEvent` schema、actor labeling、resolved evidence links
- shell 階段：不可把聊天室 UI 當 consultation transcript 正式版

#### 13.3.4 CW-03 Committee Board

- 目標頁面：board list、participant roster、escalation reason、sponsor decision、synthesis summary
- 期待 BFF：committee board projection、sponsor decision authority、synthesis summary shape
- shell 階段：不可從 participant votes 自己算 verdict

#### 13.3.5 CW-04 Red-team Memo

- 目標頁面：memo list/detail、findings summary、recommendations、evidence drawer、governance handoff
- 期待 BFF：memo list/detail、session-to-memo mapping、`canInitiateGovernanceReview`
- shell 階段：不可憑 L3 設計稿發明 publish workflow

---

## 14. Trainer Workbench

### 14.1 使命

把 demo-grade trainer shell 升級為真正的 BFF-backed teaching workflow。

### 14.2 頁面清單

| 頁面 | Path | 期待 contract | 狀態 | 目前可做 |
|---|---|---|---|---|
| Trainer Landing | `/trainer` | workbench shell | blocked | shell-only |
| Teaching Dialog / Session List | `/trainer/sessions` | `TW-01` | contract-published | pending-bff placeholder only |
| Teaching Dialog Detail | `/trainer/sessions/:session_id` | `TW-01` | contract-published | pending-bff placeholder only |
| Parameter Controls | `/trainer/sessions/:session_id/controls` | `TW-02` | blocked | shell-only |
| Before/After Compare | `/trainer/sessions/:session_id/compare` | `TW-03` | blocked | shell-only |
| Teaching Replay List | `/trainer/replay` | `TW-04` | blocked | shell-only |
| Teaching Replay Detail | `/trainer/replay/:session_id` | `TW-04` | blocked | shell-only |

### 14.3 頁面定義

#### 14.3.1 Trainer Landing

- 目的：說明 trainer workflow 的四步驟：
  - dialog
  - parameter controls
  - compare
  - replay
- 現階段只能做 shell，不得把 demo-grade UI 當正式 Trainer 真相

#### 14.3.2 TW-01 Teaching Dialog

- 目標頁面：session start、transcript panel、message composer、session list
- 已發布 contract：`docs/bff/TW-01-teaching-dialog.md`、`docs/screens/TW-01-teaching-dialog.md`、`docs/examples/TW-01-teaching-dialog.json`
- 前端 handoff：`docs/pantheon-handoffs/TW-01-teaching-dialog/FRONTEND_CHANGE_SPEC.md`
- 目前 gate：BFF 必須先讓 create/list/detail/message routes 上線並回傳已發布 trainer-session 與 `TeachingEvent` field shape
- pending-BFF 階段：可做明確 blocked placeholder；不可用本地 message state 拼 transcript，也不可把 Persona teaching history 當 Trainer workflow 替代品

#### 14.3.3 TW-02 Parameter Controls

- 目標頁面：control state panel、patch editor、validation warnings、inline diff
- 期待 BFF：controls read route、patch route、control schema、validation contract
- shell 階段：不可把 slider / form 直接接本地 state 當產品真相

#### 14.3.4 TW-03 Before/After Compare

- 目標頁面：metric panels、warning hierarchy、control diff、rapid-eval summary
- 期待 BFF：preview route、preview contract、`preview_unavailable` degraded contract
- shell 階段：不可用 mock performance chart 假裝是正式 preview

#### 14.3.5 TW-04 Teaching Replay

- 目標頁面：session history、ordered event timeline、evidence drawer、commit/discard authority、replay cursor
- 期待 BFF：full `TeachingEvent` schema、commit/discard routes、before/after artifact refs
- shell 階段：不可把 Persona teaching history 誤當 Trainer replay

---

## 15. Lovable 的實作順序

### 15.1 第一層：先做系統，不要先做頁

先做：

1. shared app shell
2. sidebar + top bar
3. Global Degradation Banner
4. SurfaceStateRenderer
5. QueueShell / BoardShell / GraphShell / CompareShell / EvidenceDrawer
6. SSE substrate 與 realtime footer rail

### 15.2 第二層：先做已 ready / contract-ready 的 workbench

建議順序：

1. Operator Console
2. Governance
3. Persona
4. Evolution baseline

### 15.3 第三層：做 overview-only workbench

在沒有新 BFF 之前，只能做：

1. Knowledge Overview
2. Consultation Overview
3. Research Overview
4. Trainer shell landing

### 15.4 第四層：blocked 模組只有在 packet 開啟後才能轉正式實作

以下在 BFF 未完成前，不得開 production page：

- `EW-04`, `EW-05`
- `RW-01` 到 `RW-05`
- `KW-01` 到 `KW-05`
- `CW-01` 到 `CW-04`
- `TW-01` 到 `TW-04`

---

## 16. 發現 BFF gap 時怎麼做

若前端發現必要欄位缺失或 authority 不真實：

1. 不要補猜
2. 不要用假值 render production UI
3. 停止該面板的正式交付
4. 建立 `.coordination/requests/PKT-xxx-bff-gap.yaml`
5. 在 handoff 中明確說出缺哪個欄位、哪條路由、哪個 CTA authority

---

## 17. Lovable 絕對不能做的事

- 把 blocked module 做成 production-ready 功能
- 把 overview-only page 擴寫成真的 module detail
- 用前端邏輯補 backend 缺失
- 在 summary page 放 write authority
- 把 degraded 靜默變成 empty
- 用 local mock data 假裝 packet 已 ready
- 用舊 route 或 raw route 重建本該由 BFF composed 的 object

---

## 18. 一句話交付原則

Lovable 不是在做一堆頁面，而是在做一個有共同殼層、共同 primitive、共同 authority 規則、共同 degraded/realtime 語意的 Pantheon 前端系統。
