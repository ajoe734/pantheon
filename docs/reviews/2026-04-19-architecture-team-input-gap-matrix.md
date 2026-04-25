# 2026-04-19 Architecture Team Input Gap Matrix

## 目的

這份文件不是在重寫 Pantheon 的高階藍圖。

高階藍圖已經存在，包含：

- 八大 workbench 的產品邊界
- 各 workbench 的頁面 IA
- Wave 順序與依賴關係
- Lovable 不可發明資料與不可越權的實作規則

目前真正卡住的，是一批 **模組級 canonical contract / BFF-facing design 還沒補齊** 的地方。

這份文件要給系統設計架構團隊明確回答：

1. 哪些模組不是缺前端，而是缺 module-level design
2. 每個模組還缺哪一類 canonical input
3. 補齊後，Pantheon 才能把 packet / BFF / screen / frontend handoff 接起來

---

## 結論先講

這些未完成項目 **大多不是高階藍圖缺失**，而是：

- 高階藍圖已存在
- module-level route / object / authority / lifecycle / degradation contract 尚未定案或尚未落成 canonical BFF truth

也就是說，現在要架構團隊補的是：

1. **模組級讀路由**
2. **模組級 read model / composed object**
3. **必要的 write path / command vocabulary**
4. **`allowedActions` authority gating**
5. **`meta.surfaces.*` degraded / unavailable semantics**
6. **lifecycle / state machine**
7. **必要 filter / pagination / identity semantics**
8. **example payload 與 screen-handoff 前置條件**

---

## 所有 blocked module 都應補齊的共通最小包

架構團隊若要讓某個 blocked module 變成可開 packet，至少要補這 8 件：

1. `Primary read route`
   例：`GET /api/v1/...`

2. `Primary read model`
   也就是前端真正 render 的 composed object，不是 raw service object，也不是 storage schema。

3. `Identity + lifecycle`
   要明確定義主鍵、可見 state、state transition、哪些 state 只是 evidence、哪些 state 真的影響 CTA。

4. `Authority`
   若該模組有寫入，必須提供 `allowedActions.*` 或明確 write contract。

5. `Degradation semantics`
   至少要有 `meta.surfaces.<surface_name>`，必要時還要定義 `partial`、`stale`、`preview_unavailable` 等受控狀態。

6. `Filter / pagination / ordering`
   必須是 backend-owned truth，不能把排序和過濾丟給前端猜。

7. `Non-goals / client must not synthesize`
   要明確說前端不能從哪些 raw route 自行拼資料。

8. `Example payload / handoff readiness`
   沒有 example payload 與 BFF truth，Lovable 只能做 shell，不能做 production UI。

---

## A. Evolution Workbench

高階藍圖狀態：

- `EW-01~03` 已有 baseline
- `EW-04`、`EW-05` 的 high-level purpose 已存在
- 真正缺的是 operator-facing module contract

來源：

- [WORKBENCH_DELIVERY_BACKLOG.md](/home/edna/code/pantheon/WORKBENCH_DELIVERY_BACKLOG.md:56)
- [EW-004 Packet Family](/home/edna/code/pantheon/docs/pantheon-handoffs/EW-004-evolution-workbench/PACKET_FAMILY.md:108)

### A1. EW-04 Inspiration Graph

架構團隊要補：

- `GET /api/v1/lineage/inspiration/{artifact_id}`
- inspiration graph composed object
- `meta.surfaces.inspiration`
- edge detail 的 canonical field shape
- strategy tags rail 的 canonical field shape
- `meta.snapshot_at`
- graph degradation / suppression rules

最低需要鎖定的 payload 內容：

- `artifact_id`
- `inspiration_edges[]`
  - `source_artifact_id`
  - `relationship_type`
  - `influence_weight`
- `strategy_tags[]`
- edge detail drawer 所需欄位
- `meta.snapshot_at`
- `meta.surfaces.inspiration`

還要明確寫出的約束：

- 前端不得用 `GET /api/v1/lineage`
- 前端不得用 `GET /api/v1/lineage/graph`
- 前端不得自己從 raw lineage edges 拼 inspiration graph

一句話：

`EW-04` 不是缺畫 graph，而是缺 **一條 dedicated inspiration route + 一個 dedicated inspiration read model**。

### A2. EW-05 Mutation Review

架構團隊要補：

- `GET /api/v1/operator/mutation-review/{decision_id}`
- mutation-review composed object
- `ApproveMutation` / `RejectMutation` command vocabulary
- `allowedActions.canApproveMutation`
- `allowedActions.canRejectMutation`
- `meta.surfaces.mutation_review`

最低需要鎖定的 payload 內容：

- decision context header
  - `decision_id`
  - `action_type`
  - `risk_level`
  - `decision_state`
  - `approval_decision_id`
- `proposed_changes`
- `risk_assessment`
- `required_approvals`
- incident/postmortem evidence refs
- rollback follow-through refs
- `allowedActions`

還要明確寫出的約束：

- Mutation Review 只能做 review authority，不是 runtime / rollback / deployment 的 write owner
- 前端不得從 `risk_level` 或 actor role 自己推斷 approve/reject CTA
- degraded / unavailable 時 CTA 必須消失，不是 disabled-only

一句話：

`EW-05` 缺的不是抽象意圖，而是 **operator 可審的單一 read model + 真實 authority model**。

---

## B. Research Workbench

高階藍圖狀態：

- `RW-01~05` 的模組順序與 purpose 已有
- 缺的是整組 research plane 的 BFF canonical contract

來源：

- [WORKBENCH_DELIVERY_BACKLOG.md](/home/edna/code/pantheon/WORKBENCH_DELIVERY_BACKLOG.md:63)
- [RW-005 Packet Family](/home/edna/code/pantheon/docs/pantheon-handoffs/RW-005-research-workbench/PACKET_FAMILY.md:32)

### B1. RW-01 Research Ticket

架構團隊要補：

- `POST /api/v1/research/tickets`
- `GET /api/v1/research/tickets`
- `GET /api/v1/research/tickets/{ticket_id}`
- `PATCH /api/v1/research/tickets/{ticket_id}`
- ticket lifecycle contract
- `allowedActions` for edit / close / archive

最低需要鎖定的 contract：

- identity: `ticket_id`
- lifecycle: `open -> in-progress -> closed -> archived`
- list filters:
  - `status`
  - `owner`
  - `page_token`
  - `page_size`
- detail fields:
  - title
  - description
  - status
  - lifecycle history
  - linked experiments
  - linked artifacts
  - `allowedActions`

### B2. RW-02 Search

架構團隊要補：

- `GET /api/v1/research/search`
- search index adapter
- search result object
- filter semantics

最低需要鎖定的 contract：

- query params:
  - `q`
  - `match_type`
  - `status`
  - `date_range`
  - `page_token`
  - `page_size`
- result row:
  - `result_id`
  - `match_type`
  - `title`
  - `excerpt`
  - `linked_ticket_id`
  - `relevance_score`
- `meta.surfaces.search_results`

明確非目標：

- 前端不能把 ticket/experiment/artifact 讀下來自己做搜尋

### B3. RW-03 Analyze

架構團隊要補：

- `GET /api/v1/research/analysis`
- `GET /api/v1/research/analysis/{analysis_id}`
- metric aggregation contract
- comparison payload shape

最低需要鎖定的 contract：

- list filters:
  - `ticket_id`
  - `experiment_id`
  - `status`
  - `date_range`
- detail:
  - `analysis_id`
  - `status`
  - `summary`
  - backend-grouped metric panels
- `meta.surfaces.analysis_results`

明確非目標：

- 前端不得拿 raw result 自己 grouping

### B4. RW-04 Experiment Launch

架構團隊要補：

- `POST /api/v1/experiments/launch`
- `GET /api/v1/experiments/{experiment_id}`
- `GET /api/v1/experiments`
- `POST /api/v1/experiments/{experiment_id}/cancel`
- experiment state machine
- `allowedActions.canCancel`

最低需要鎖定的 contract：

- create body:
  - linked ticket
  - parameter set
  - algorithm/strategy selector
  - run config
- status machine:
  - `queued`
  - `running`
  - `completed`
  - `failed`
  - `canceled`
- detail:
  - `experiment_id`
  - `status`
  - `progress`
  - `artifact_ids`
  - `meta.surfaces.experiment_status`

### B5. RW-05 Artifact Compare

架構團隊要補：

- `GET /api/v1/artifacts`
- `GET /api/v1/artifacts/{artifact_id}`
- `GET /api/v1/artifacts/compare`
- artifact versioning semantics
- compare diff contract

最低需要鎖定的 contract：

- artifact identity
- version ancestry
- compare response:
  - `field_pairs`
  - `change_labels`
  - `delta_magnitudes`
- provenance:
  - linked experiment
  - linked ticket
  - lineage refs

明確非目標：

- 前端不得拿兩份 JSON 自己比 diff

---

## C. Knowledge Workbench

高階藍圖狀態：

- overview packet 已 live
- `KW-01~05` 的 purpose 與 wave order 已存在
- 缺的是 browse/detail/versioning/aggregation 級 contract

來源：

- [WORKBENCH_DELIVERY_BACKLOG.md](/home/edna/code/pantheon/WORKBENCH_DELIVERY_BACKLOG.md:75)
- [KW-006 Packet Family](/home/edna/code/pantheon/docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md:48)
- [PKT Knowledge Overview BFF](/home/edna/code/pantheon/docs/bff/PKT-knowledge-workbench.md:1)

### C1. KW-01 Institutional Memory

架構團隊要補：

- `GET /api/v1/knowledge/memory`
- `GET /api/v1/knowledge/memory/{entry_id}`
- institutional memory browse projection
- memory entry lifecycle and identity contract

最低需要鎖定的 contract：

- identity: `entry_id`
- canonical type field 到底是不是 `knowledge_type`
- lifecycle:
  - `draft`
  - `active`
  - `archived`
- list filters:
  - `knowledge_type`
  - `scope`
  - `scope_filter`
  - `tag`
  - `query`
- detail fields:
  - `content.headline`
  - `content.body`
  - `structured_payload`
  - `source_event_type`
  - `source_event_id`
  - `contributing_persona_ids`
  - `superseded_by`

### C2. KW-02 Research Notes

架構團隊要補：

- `POST /api/v1/knowledge/notes`
- `GET /api/v1/knowledge/notes`
- `GET /api/v1/knowledge/notes/{note_id}`
- ownership contract
- attachment taxonomy

最低需要鎖定的 contract：

- `owner_ref`
- attachment target taxonomy
  - `research_ticket`
  - `persona`
  - `strategy_spec`
  - `free_standing`
- referential integrity rules
- note body shape
- linked evidence refs

### C3. KW-03 Evidence Refs

架構團隊要補：

- `GET /api/v1/knowledge/evidence`
- `GET /api/v1/knowledge/evidence/{ref_id}`
- evidence reference read model
- evidence link resolution contract

最低需要鎖定的 contract：

- source-document identity
- link taxonomy
- linked target refs
- credibility metadata
- BFF-resolved canonical links

明確非目標：

- 前端不得從 raw `storage_ref` 或 `ref_id` 猜 URL

### C4. KW-04 Insight Cards

架構團隊要補：

- insight aggregation endpoint
- insight detail endpoint
- card-surface read model
- filter taxonomy and aggregation contract

最低需要鎖定的 contract：

- card identity: `insight_id`
- summary
- scope
- confidence
- supporting evidence refs
- linked-source drilldown contract
- filters:
  - tag
  - linked entity
  - recency

明確非目標：

- 前端不得 client-side 聚合 memory + notes + evidence

### C5. KW-05 Strategy Spec

架構團隊要補：

- strategy-spec list route
- versioned detail route
- versioning and lifecycle contract
- diff/compare contract

最低需要鎖定的 contract：

- identity: `strategy_id`
- version selector semantics
- lifecycle:
  - `draft`
  - `approved`
  - `deprecated`
- citation bundle
- compare payload
- ancestry semantics

明確非目標：

- 前端不得直接比較 raw spec JSON

---

## D. Consultation Workbench

高階藍圖狀態：

- overview packet 已 live
- `CW-01~04` 的 module purpose 與順序已存在
- 缺的是 request/transcript/committee/memo 的實際 workbench contract

來源：

- [WORKBENCH_DELIVERY_BACKLOG.md](/home/edna/code/pantheon/WORKBENCH_DELIVERY_BACKLOG.md:87)
- [CW-008 Packet Family](/home/edna/code/pantheon/docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md:48)
- [PKT Consultation Overview BFF](/home/edna/code/pantheon/docs/bff/PKT-consultation-workbench.md:1)

### D1. CW-01 Consult Request

架構團隊要補：

- `POST /api/v1/consult/requests`
- `GET /api/v1/consult/requests`
- `GET /api/v1/consult/requests/:request_id`
- `POST /api/v1/consult/requests/:request_id/cancel`
- `ConsultRequest` lifecycle contract

最低需要鎖定的 contract：

- request body:
  - `from_persona_id`
  - `target_type`
  - `target_ref`
  - `task`
  - `context_refs`
  - `priority`
  - `consultation_type`
- lifecycle:
  - `created`
  - `running`
  - `completed`
  - `canceled`
- `linked_session_id`
- `allowedActions.canCancel`

### D2. CW-02 Debate Transcript

架構團隊要補：

- `GET /api/v1/consultations/:session_id/transcript`
- append-only `TranscriptEvent` schema
- actor labeling contract
- inline evidence behavior

最低需要鎖定的 contract：

- `event_id`
- `session_id`
- `sequence_number`
- `actor_id`
- `actor_role`
- `event_type`
- `body`
- `evidence_ref`
- `evidence_link`
- `emitted_at`
- `meta.surfaces.transcript`
  - 含 `partial | degraded | unavailable` 的行為

明確非目標：

- 前端不得從 raw participant refs 自己解 actor label

### D3. CW-03 Committee Board

架構團隊要補：

- `GET /api/v1/committees`
- `GET /api/v1/committees/:committee_id`
- committee board projection
- `RecordSponsorDecision` command
- synthesis summary shape

最低需要鎖定的 contract：

- `committee_ref` identity
- `participant_roster[]`
- `quorum_state`
- `consensus_state`
- `escalation_reason`
- `synthesis_summary`
  - `outcome`
  - `rationale_ref`
  - `evidence_refs[]`
  - `dissent_refs[]`
- `allowedActions.canRecordSponsorDecision`

明確非目標：

- 前端不得從 participant votes 自己算 committee verdict

### D4. CW-04 Red-team Memo

架構團隊要補：

- `GET /api/v1/consult/memos`
- `GET /api/v1/consult/memos/:memo_id`
- `ConsultMemo` read model
- red-team session-to-memo mapping
- `allowedActions.canInitiateGovernanceReview`

最低需要鎖定的 contract：

- lifecycle:
  - `draft`
  - `published`
- recommendation shape
- evidence-link contract
- originating request/session relationship
- governance handoff authority

需要特別說清楚：

- `archived` 不是目前已確認的 lifecycle，若要加是 net-new contract decision
- per-recommendation severity 不是目前必需，除非架構團隊要明確新增

---

## E. Trainer Workbench

高階藍圖狀態：

- module purpose 與順序已存在
- 缺的是 trainer-specific session/control/preview/replay canonical contract

來源：

- [WORKBENCH_DELIVERY_BACKLOG.md](/home/edna/code/pantheon/WORKBENCH_DELIVERY_BACKLOG.md:96)
- [TW-007 Packet Family](/home/edna/code/pantheon/docs/pantheon-handoffs/TW-007-trainer-workbench/PACKET_FAMILY.md:48)

### E1. TW-01 Teaching Dialog

架構團隊要補：

- `POST /api/v1/trainer/sessions`
- `GET /api/v1/trainer/sessions/:id`
- `GET /api/v1/trainer/sessions`
- `POST /api/v1/trainer/sessions/:id/message`
- trainer session lifecycle contract
- `TeachingEvent` schema（dialog subset）

最低需要鎖定的 contract：

- session body:
  - `persona_id`
  - `session_type=trainer`
  - `objective`
  - `context_refs[]`
- lifecycle:
  - `active`
  - `paused`
  - `completed`
  - `abandoned`
- dialog event:
  - `event_id`
  - `session_id`
  - `actor`
  - `message_body`
  - `emitted_at`
  - `sequence_number`
  - optional `outcome_signal`

### E2. TW-02 Parameter Controls

架構團隊要補：

- `GET /api/v1/trainer/sessions/:id/controls`
- `POST /api/v1/trainer/sessions/:id/patch`
- `ControlParameter` schema
- patch validation contract
- patch diff response shape

最低需要鎖定的 contract：

- control object:
  - `control_id`
  - `parameter_key`
  - `current_value`
  - `allowed_range`
  - `unit`
  - `last_modified_at`
- patch body:
  - `patches: [{parameter_key, proposed_value}]`
- validation:
  - `valid`
  - `warnings[]`
  - `applied`
- diff:
  - `previous_value`
  - `new_value`

明確非目標：

- 前端不得自己 clip 值到 `allowed_range`

### E3. TW-03 Before/After Compare

架構團隊要補：

- preview / rapid-eval route
- preview response contract
- `preview_unavailable` degraded contract
- async eval polling semantics
- `meta.surfaces.trainer_preview`

最低需要鎖定的 contract：

- `eval_id`
- `status`
  - `complete`
  - `pending`
  - `failed`
- `metric_delta[]`
- `warnings[]` with levels
- `preview_quality`
- `baseline_snapshot_at`
- `candidate_snapshot_at`
- polling interval / max wait / timeout

明確非目標：

- 前端不得用 control diff 自己推 performance preview

### E4. TW-04 Teaching Replay

架構團隊要補：

- standalone replay read route
- full `TeachingEvent` schema
- BFF-resolved evidence links
- commit contract
- discard contract
- before/after artifact refs

最低需要鎖定的 contract：

- full event types:
  - `message`
  - `control_patch`
  - `preview_trigger`
  - `outcome_signal`
  - `commit`
  - `discard`
- commit/discard gating:
  - `allowedActions.canCommit`
  - `allowedActions.canDiscard`
- session precondition:
  - only when `status = completed`
- artifact refs:
  - `before_artifact_ref`
  - `after_artifact_ref`

明確非目標：

- 前端不得把 Persona teaching history 當成 trainer replay

---

## 真正需要架構團隊交付的文件包

如果你要讓這些 module 從 blocked 進入可開發，架構團隊至少要補以下交付物：

1. `docs/bff/<module>.md`
   定義 route、query params、response shape、`allowedActions`、`meta.surfaces.*`

2. `docs/screens/<module>.md`
   定義畫面要 render 的 backend-shaped object 與非目標

3. `docs/examples/<module>.json`
   給 Lovable 與前端工程看真實 payload

4. `docs/pantheon-handoffs/<module>/FRONTEND_CHANGE_SPEC.md`
   當該 module 已經到可 handoff 時再開

5. 若有寫入：
   明確 command vocabulary 或 module-local write route contract

6. 若有 lifecycle：
   明確 state machine，不可只留在 L3 storage hints

---

## 建議你給架構團隊的一句話

不是要他們重畫整個 Pantheon，而是要他們把這些 blocked module 的 **BFF-facing canonical module contract** 補齊：

- route
- read model
- authority
- lifecycle
- degradation semantics
- example payload

補齊之前，Lovable 最多只能做 shell；補齊之後，Pantheon 才能開 honest packet、screen spec 和 frontend handoff。
