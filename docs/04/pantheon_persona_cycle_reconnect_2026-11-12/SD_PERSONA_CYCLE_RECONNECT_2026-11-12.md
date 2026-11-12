# SD-PERSONA-CYCLE-RECONNECT-20261112 — reconnect the persona cycle to the original plan: memory, consultation, human-learning surfaces and a self-optimising harness

| Field | Value |
| --- | --- |
| Status | Design for implementation; the operator asked on 2026-11-12 to write it, open the tasks and let the supervisor and auto workers deliver them |
| Date | 2026-11-12 |
| Repo base | `origin/dev` 5183cbf91; code facts were read on a7ff97105 and must be re-checked by the delivering task (§2, §3) |
| Extends | `docs/04/pantheon_strategy_discovery_sources_2026-11-10/SD_PERSONA_STRATEGY_POOL_AUTONOMY_2026-11-12.md` (target model §4, persona cycle §5.5) and its capital addendum; nothing in the pool model is replaced |
| Restores | the per-persona OODA of `Pantheon_總索引版系統分析文件.md` §6.4; the persona memory plan (BG-004, OCLAW-PMEM); the consult policy of `PERSONA_RUNTIME_MODEL.md` §6/§13; the seven-stage discussion loop; the Agora learning surfaces deleted by `AGORA-DEAD-SURFACES-REMOVAL-001` |
| Adds | evidence-driven self-optimisation of the persona harness (operator direction of 2026-11-12) |
| Scope | design only; no source, test, workflow, configuration or infrastructure change |

---

## 0. 中文摘要（操作者用）

**為什麼要這份設計：** 11/12 的策略池設計把 persona 的每日循環縮成「只看池子健康」。回頭讀一開始的規劃才發現，原本就規劃好的三件事被漏掉了：
- persona 的記憶；
- persona 之間互相會診、紅隊挑戰；
- 人教 persona 的入口。

這些元件多半已經做好，只是沒有接上。10/01 還把 Agora 的委員會、記憶、persona lab、技能教練、評估等入口刪掉了。

**這份設計做九件事，都照原始規劃，只有第 8 件是新的：**
1. **每日循環真的跑起來。** 每個 persona 每天由排程喚醒自己的 agent 想一輪，留一份五段紀錄（觀察、判斷、決定、行動、學習）。agent 只提案；規則和關卡做決定；交易路徑上不放模型。
2. **每個交易 persona 都有自己的 agent。** 建立時就配好；開工前把記憶放進它的工作區。
3. **記憶真的寫得進去、讀得出來。**
   - 寫：由擁有該事件的服務在事件結束時寫，包括事後檢討、演化、研究發現、池子進出、會診結束、交易教訓。
   - 讀：每輪開工、寫需求單、會診時讀。
   - agent 自己只能提出候選，不能直接寫。
4. **會診照每個 persona 的會診規則自動發起。** 例如進池前要問風控 persona，高風險修改不能單獨做。
   - 參加者是真的 persona（發起者、風控、紅隊），各自先獨立回答，再整理共識與分歧。
   - 風控 persona 說「反對」時，paper 內也不會自動通過，改送你決定。
   - 結論寫進記憶。
5. **人在對話裡核准的建議有下游。** 「做研究」變成研究單，「記憶候選」進記憶審核，「persona 變更」進 harness 流程。
6. **①② 同步：** 需求單接到 6 月已做好的「persona 策略探索」，不另做一套。
7. **恢復 10/01 刪掉的入口，這次接到真的擁有者：** 委員會、記憶審核、persona lab、技能教練、評估、訊號回饋、筆記、洞察、訓練範例、變更紀錄（接受、復原、不要再這樣）、shadow 對照。
8. **harness 自己優化（新）。** persona 依實際軌跡提出對自己守則、會診規則、工具、技能、流程、門檻的修改。流程是：
   - 先過「不得比現況寬鬆」的檢查；
   - 用同一批觀察新舊各跑一次比較，「維持原狀」也是合法結果；
   - paper 內由自動關卡加風控 persona 簽核後採用，可回滾；
   - 觀察期後標記「有幫助、有害、沒差、無法判斷」，存進經驗庫；
   - 超出 paper 一律要你核准；風險與資金上限永遠不在可改範圍。
9. **⑩ 照 7/05 原規劃。** 分數對應建議用固定規則，AI 只寫理由（你 11/12 的決定）。各種建議送到各自的擁有者：
   - 增加研究與工具權限 → harness 流程；
   - 要求重訓 → 策略池演化；
   - 減資 → paper 分配；
   - 凍結、退役 → 照舊；
   - canary 候選在送到你面前之前，先由風控與紅隊 persona 審一次。

**你的界線不變：** 超出 paper、persona 生命週期、風險與資金上限、真實資金、canary 核准、牽涉風險或資金的教訓，都要你核准。

**交付：** 6 波、19 筆任務，全部排在本設計審查合併之後。既有的策略池任務不重開；只有「每日自主循環」那筆會加上前置條件與說明。

---

## 1. Operator statements of 2026-11-12 (verbatim)

> persona的記憶跟各persona的討論會議機制是怎樣進行? 這些你詳細跟現在的L1-L12怎麼協作

The operator then supplied the cycle diagram reproduced in §5 and asked:

> 跟這些到底互動關係是什麼?你詳細思考整理出來

When the coordinator answered with five questions for the operator to decide, the operator rejected being asked to decide what the original planning already settled and instructed:

> 你完整把規劃以及原先作法找出來

> 另外給你方向你要變成harness也能夠自己優化

On the Agora surfaces deleted on 2026-10-01 (committee, memory, skill coaching, persona lab, evaluation):

> 這些感覺起來應該是要有的阿

> 好，加入完整版

> 寫成正式設計，也把任務開起來做一做，12同步也做一做 10原本的規劃加入完整版，並且把任務讓supervisor 跟auto worker做一做

Reading of the last statement, stated so the reviewer can check it:
- 「12同步」 means connecting steps ① and ② of the diagram to the existing persona strategy discovery (§6.6).
- 「10原本的規劃」 means the 2026-07-05 promotion governance plan for step ⑩ (§6.9).

Decisions recorded on 2026-11-12 by an earlier session and kept here: the canary candidacy path is built only up to the approval; the nomination rule is fixed and the agent writes the rationale only. Both are in task `PAPER-CANARY-PROMOTION-PROPOSAL-20261112`.

---

## 2. What the original planning already decided

Authority order (`CANONICAL_DOCUMENT_MAP.md:28-50`):
- L1 outranks L2; inside L1 the narrower document wins.
- `Pantheon_總索引版系統分析文件.md` is L3 background (`:36`).
- An operator decision outranks a team design.
- After that, the newest document that has not been superseded wins.

### 2.1 Persona cycle

| Planned | Source | Built at a7ff97105 |
| --- | --- | --- |
| Each persona runs its own Observe/Orient/Decide/Act/Learn. Personas may consult and red-team each other; their loops are never merged into one. | 總索引 axiom 11 `:112`, §6.4 `:389-409` | no persona-level controller |
| Observe includes Agora, trainer and trader feedback. Act produces proposals, evidence, ConsultMemo, candidate and allocation artifacts, never orders. Learn includes postmortem, evolution and teaching history. | 總索引 `:116`, `:389-409` | only the evidence-packet contract exists |
| Each cycle run leaves one OodaLoopPacket. | `docs/04/pantheon_sa_supplemental_2026-05-15/SA_management_console_multi_persona_ooda.md` FR-11; SD §3.2 | packet store built; one empty packet at persona create |
| Creating a persona registers its cron jobs, and the cron wakes the persona's own agent to produce proposals. Operator: 「建人格就該完成第三步」. | `.orchestrator/task-briefs/openclaw_persona_ooda_loop_wiring.md:13` | today only one `*/15` first-evaluation job (`services/control-plane/cron/workflows.py:75`, `services/control-plane/bff/personas/service.py:4414`) |
| One cron run = one real agent turn = one packet or one explicit terminal failure; Act is proposal-only; a controller reconciles the eligible persona set. | `LOOP_PRODUCT_LEVEL_REMEDIATION_PLAN_2026-07-13.md:362-381` (`:376`); `docs/bff/execution-tasks/2026-07-13-loop-product-level-remediation/LOOP-PROD-OODA-001.md` | `services/persona/cron_ooda_closure.py` is called only by a manual script |
| The per-persona OODA is a composite overlay of L1–L8, L10 and L11. | `docs/deployment/loop-catalog.registry.json` `composite_overlays.per_persona_ooda`; `docs/05/system-verification-rounds/INDEX.md:21-25` | `controller_contract.status: not_implemented` |
| Fate of that task. | false closure `docs/deployment/evidence/loop-product-level/closeout-truth-audit-2026-07-16.md:28`; security hold `archive/SECURITY_PREFLIGHT_AND_HOLD_MATRIX_2026-07-18.md:22-31`; removed from loop health 2026-08-28 | no cancellation recorded |

### 2.2 Memory

| Planned | Source | Built |
| --- | --- | --- |
| Private PersonaMemory and shared InstitutionalMemory. 「No session (interactive or research) may write directly」; whitelisted owner services write when a lifecycle event ends. | `services/memory/MEMORY_LAYER_DESIGN_NOTE.md:77-89`; triggers §4.1 `:108-118`; readers §5.2 `:180-189` | service deployed; no writer runs |
| The OpenClaw MEMORY.md is a cache built from the Memory Plane. 「canonical memory feed OpenClaw, not the reverse」; the agent may only propose writebacks. | `OPENCLAW_PERSONA_MEMORY_GAP_SPEC.md:83-95`, `:127-133` | bridge built; only a manual script materialises the cache |
| Trade lessons are candidates: proposed → pending_review → endorsed → merged. | `PERSONA_TRADE_JOURNAL_GAP.md:109-111` | review routes deployed; nothing creates candidates in the memory service |
| Negative memory: a seed that resembles rejected or retired work is blocked. | `EPIC_INTERACTION_DERIVED_SEEDS.md` IDS-007 | running in the seed store |

### 2.3 Consultation and meetings

| Planned | Source | Built |
| --- | --- | --- |
| Each persona has a Consult Policy: required reviewers, committees, trigger rules and forbidden solo actions. Examples: 「上 paper 前必須 consult risk persona」, 「高風險 patch 不得 solo 推進」. | `PERSONA_RUNTIME_MODEL.md` §6 (`:177`); 總索引 `:785` | stored and displayed only |
| A persona can be requester, responder or committee participant. 「researcher / persona 建立 consult request」. | `PERSONA_RUNTIME_MODEL.md` §13 (`:297`); 總索引 §9.4.4 `:752`; `services/control-plane/persona/contract.md:134-142` | no persona-started consult |
| Seven stages 討論→建議→模擬→決策→執行→回顧→學習. A sponsor persona certifies the suggestion; an operator or a reviewer persona signs the decision; Learning goes into persona memory. | `SEVEN_STAGE_DISCUSSION_LOOP_SPEC.md:61`, `:71`, `:86`; adapters `:124-149` | committee adapter only; its dispatch falls back to localhost (`services/consultation/main.py:1237`) |
| Multi-persona arbitration: hard veto, weighted fusion, committee override. | `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md:78-85`, `:215-227` | implemented; no caller |
| A high-risk allocation approval needs a committee memo plus a fresh handoff. | MPOS-P1-CONSULT-001; `services/control-plane/governance/approval_decision.py:169` | enforced; no flow creates such approvals |
| Committee voting and quorum are deferred; the consultation session protocol was never written. | `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md:205-214`; `OPENCLAW_RUNTIME_CONTRACT.md:509-517` | — |

### 2.4 Human learning and recommendations

| Planned | Source | Built |
| --- | --- | --- |
| Agora interaction becomes `persona memory / strategy lesson / imitation dataset`. A persona-lab commit becomes a `trainer_feedback_to_persona_update` handoff to management review. | 總索引 `:415-432`, `:747` | surfaces deleted 2026-10-01 |
| Typed proposals from conversation include a research request, a memory update candidate and a policy or mutation review. They must 「never silently update」 memory. | `docs/product/persona-interaction-and-governed-action-plan.md:218-231`, `:305`; narrowed by the 2026-07-17 plan `:41` | stored; no consumer |
| A teaching result must pass evaluation before it changes a persona. | `LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md` §3.4 | training target stored, read by nothing |
| The Agora servant keeps an evidence-cited change log with Accept / Undo / Never do this again, evaluates 「AI 改 dashboard 後有沒有變好」 and tracks 「改了之後變好還是變差」. Shadow arms: base_strategy / private_servant / human_actual / committee_variant. | UI spec 2026-05-20 `:137`, `:1020`; V5 `:694`; A4 `:41`, `:193` | never built; surfaces deleted |

### 2.5 Evolution and the persona's own policy

| Planned | Source | Built |
| --- | --- | --- |
| `mutate` changes a persona's route policy, consult policy, risk tolerance and allowed tools. It is medium risk and approved by the Risk Owner; split and merge go to the Governance Committee. The proposer is the Evolution Controller; v1 sets `auto_approve_allowed: false`. | `EVOLUTION_REVIEW_AND_THRESHOLDS.md:46`, `:221`, `:266-271` | enums only; `evolution_controller_contract.md:70` has no persona-mutation route |
| DSPy optimises the persona's chat and tool-routing prompts. Operator feedback is the only signal. draft→candidate passes an automated gate that must not become 「materially more permissive」; paper and live need the operator. | `services/control-plane/persona/lp001/contract.md` | adapter runs on fixtures only |
| PersonaPatch (`route_policy|consult_policy|memory|behavior|tool_profile`) goes draft → previewed → committed. | `docs/03/SD-02_persona_governance.md:214-235` | not built |
| Never let a persona 「mutate live behavior directly from short-term market feedback」. | `TARGET_ARCHITECTURE.md:14` | — |
| Agents read data and propose; a human approves; the owner service executes. 「不新增通用 harness」 is scoped to the research single-owner boundary. | `docs/02-architecture/phase4-bff-thinning.md:11`, `:86` | — |
| Paper capital operations skip owner approval and separation of duties; low-risk approvals may be decided by `automated_gate`. | operator decision 2026-10-06/08, `services/governance/contract.md:406` | enforced |

### 2.6 Steps ①② and ⑩

| Planned | Source | Built |
| --- | --- | --- |
| Persona Strategy Discovery: 「讓 Persona 依照自己的初始設定，查找相似策略種子或策略規格」. Recommended actions are `create_research_ticket`, `promote_seed_candidate`, `run_rapid_eval`, `request_data_backfill`, `ignore`. | `DATA_STRATEGY_SOURCE_SYSTEM_DESIGN.md:35-38`, `:527`; closeout `CLOSEOUT_DATASTRAT_PERSONA_005.md` | scorer and BFF route built; operator-triggered only |
| Promotion governance: lifecycle `paper_running → canary_candidate → …`. A deterministic score-to-recommendation mapping includes `promote_to_canary_candidate`, increase research and tool access, `require retraining`, reduce capital, freeze, suspend and retire. Flow: recommendation → submit → promotion review → human decision → receipt → governed command. | `PERSONA_PROMOTION_GOVERNANCE_GAP_SPEC.md:21`, `:117-125` | submit and decision commands retired (`services/control-plane/bff/command_adapters/retired.py:5-22`); an LLM picks recommendations |

## 3. What runs on hosted dev (read only, 2026-11-12 13:00–15:00Z)

| Area | Observed |
| --- | --- |
| Memory service | Data directory empty: 0 private, 0 institutional, 0 lesson candidates. No governance authorization address, so retrieval fails closed by code (`services/memory/main.py:428`). |
| Persona cycle | Configured packet store file absent (0 packets). OpenClaw has 4 cron jobs, all `*/15` first-evaluation. 74,559 run logs, all with 0 tokens. |
| Persona agents | Only `main`, `structured-extraction`, 2 `agora-servant` and 7 `persona-opinion` agents. The trading personas (青禾 R3, 衡岳 R2, 澄川 R2) have no agent of their own. |
| Trade reflections | 2 rows, on one release-probe persona. The evaluator's pattern review reports 152 no-op per run. |
| Interactions and consultation | 7 Agora interactions (10/03–11/07); 5 capability snapshots. Consultation files last written 11/01; 6 governance handoffs. |
| Evolution | 664 decisions, all `flag_for_review` or `freeze` derived from incidents. |
| Strategy pool | Table `persona.strategy_pools` absent. `PERSONA_REGISTRY_BASE_URL` is unset on the persona service. |
| Discovery allocator | `DISCOVERY_ALLOCATOR_ENABLED=true` on research-orchestrator-svc, contrary to the 2026-11-12 decision to switch it off. |
| Consultation addresses | No governance, evolution or memory address configured. |

## 4. Decisions in this design

Each decision follows the cited original plan unless marked **change**. Numeric values are coordinator starting values, configurable and recorded in policy, not operator decisions.

| # | Decision | Follows |
| --- | --- | --- |
| D1 | The daily cycle is the per-persona OODA overlay driven by a controller. A persona cron job of a new workflow `pantheon.persona.daily-cycle` wakes the persona's own agent; one run gives one turn and one packet or one terminal failure. Start value: weekdays 10:00Z, after the Taiwan refresh. It replaces the four 2026-06 workflows for trading personas; first-evaluation stays for provisioning. | 2026-06-16 brief; LOOP-PROD-OODA-001; pool SD 「不另外做一套排程」 |
| D2 | Mixed cycle. The agent writes Orient and proposals; deterministic rules and gates decide. Risk rules (weight halving, removal on drawdown) run every cycle even when the agent fails. No model call on the trade path. | 總索引 §6.4 Act; operator 2026-11-07 |
| D3 | Every trading persona gets its own OpenClaw agent at provisioning through the existing reconciler. SOUL is rendered from the registry and the harness version. Memory is materialised before every cycle turn. The SOUL line telling the agent to update MEMORY.md is replaced by "stage candidates". | OCLAW-PMEM; `integrations/openclaw/persona_agent_sync.py:202` contradiction |
| D4 | Memory writers are the owning services when an event ends: learning bridge for postmortem and evolution, research for findings, persona service for pool outcomes and harness experiences, consultation service for consult outcomes. The agent stages candidates only. Execution never reads memory. | BG-004; OCLAW-PMEM; L1 axiom 13 |
| D5 | **Change.** Lesson candidates that affect paper behaviour only are endorsed by the existing evaluation gates (minimum sample, cross-regime) plus a risk-persona review. Lessons touching risk, capital, policy or live keep human endorsement. | extends `PERSONA_TRADE_JOURNAL_GAP.md:109-111` under paper autonomy (P-4, 2026-10-06 decision) |
| D6 | The Consult Policy gets a trigger engine evaluated at Decide. v1 rules: trial admission or envelope publication → risk persona; any harness patch → risk persona; a patch touching guardrails or consult policy → also red team. A macro-regime rule is defined but stays off until a governed regime signal exists. | `PERSONA_RUNTIME_MODEL.md` §6 |
| D7 | Panel (fills the deferred quorum and protocol). Initiator plus risk persona plus red-team persona, each answering independently from its own agent with its committee-visible memory, then one synthesis of agreements and disagreements. No vote; the initiator certifies as sponsor. A risk-persona `oppose` blocks automatic adoption and turns the item into an operator decision. | L1 axiom 3 「風控有否決權」; seven-stage Stage 2/4; `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md:213` |
| D8 | Two non-trading consultable personas are provisioned through the normal create path: a risk persona and a red-team persona. | 總索引 §7.2 「有些永遠只是 research / risk / red-team persona」 |
| D9 | Approved conversation measures get consumers. `research_request` → discovery research ticket; `memory_candidate` → memory candidate review; `persona_mutation_review` → harness patch proposal. | seven-stage adapters 4.2/4.3; 2026-07-12 plan |
| D10 | **Change (operator direction 2026-11-12).** Harness self-optimisation, §6.8. Inside paper, adoption is automatic after the pre-check, the shadow comparison and risk-persona sign-off. Outside paper, the operator approves through governance. Risk and capital limits, environment ceiling and execution authority are outside the mutable surface. | amends `EVOLUTION_REVIEW_AND_THRESHOLDS.md:266-271` for paper; LP-001 gate; SD-02 PersonaPatch; A4 arms; UI spec change log |
| D11 | The deleted Agora learning surfaces return as thin BFF forwarding over real owners. Their earlier failure was data with no owner, not the idea. | 總索引 axiom 12, §9.4.3; operator 2026-11-12 |
| D12 | ⑩ uses the deterministic 2026-07-05 mapping for every recommendation; the agent writes rationale only. Each recommendation goes to its owner. A canary candidacy carries a risk-plus-red-team memo before it reaches the operator. | promotion governance spec; operator 2026-11-12 |
| D13 | The persona cycle is shown in Management as its own health surface, not as a thirteenth canonical loop row. The catalog overlay's controller contract becomes implemented. | ACG-LOOP-PROJECTION 2026-08-28; loop catalog |

## 5. Target model against the operator's diagram

```text
persona（長期存在：條件、資金、記憶、策略池；一個帳戶）
  │ 每日循環：觀察 → 判斷缺什麼 → 決定 → 執行 → 記住結果        (D1, D2; packet per run)
  ├─ 缺策略 → ① 發需求單 → ② 找 → ③ 寫成策略 → ④ 驗證 → ⑤ 進池 → ⑥ 池子新版本上線
  ├─ 每天 → ⑦ 執行   ├─ 每天 → ⑧ 評估   └─ 每週／每 20 交易日 → ⑨ 演化 → 結果寫回記憶
每季：⑩ persona 之間排名 → 自動重分 paper 資金 → 第一名送你核准 canary 候選
```

| Step | Loops | Memory read | Memory write (owner) | Consultation | Human |
| --- | --- | --- | --- | --- | --- |
| Observe / Orient | L1, L10, L5, L4 inputs | own private and institutional, plus harness experiences | — | — | — |
| ① demand | — (persona service) | tried families, lessons, negative memory | demand record (research owner) | — | — |
| ② find, ③ write | L1, L2 via persona strategy discovery (§6.6) | — | — | — | source enabling stays operator (O-13) |
| ④ validate | L3 gate | — | research finding: success and failure reasons (research) | red team on a gate pass that would enter a pool | — |
| ⑤ admit, ⑥ publish | L8 (envelope, auto paper approval) | — | admission outcome (persona service) | risk persona required (D6) | only on risk-persona `oppose` |
| ⑦ execute | L9 | never | — | never | — |
| ⑧ evaluate | L9, L10; reflections | — | lesson candidates (persona service → memory review) | — | sensitive lessons |
| ⑨ evolve | L11 → L3 children; L4 retrain evaluator | lessons, experiences | evolution outcome (learning bridge); removal reason (persona service) | risk persona on removal or replacement | beyond paper |
| Learn (harness) | — | experiences | harness experience (persona service) | risk persona, plus red team for guardrail changes | beyond paper |
| ⑩ quarter | rankings, governance | — | — | risk plus red team on the canary candidate | canary approval (operator) |

## 6. Components

### 6.1 Per-persona OODA controller (C1)

- Desired state: every persona in an eligible lifecycle state (`paper_running`, `paper_owner`, `consultable`) has exactly one daily-cycle job, or a first-evaluation job before its first evaluation. Actual state: OpenClaw cron jobs, cron runs, packets. Missing and orphan jobs are repaired.
- Per run:
  1. A deterministic Observe bundle: pool health and member virtual books, own telemetry, open incidents, new human feedback references, retrieved memory and experiences, harness version.
  2. One turn of the persona's own agent with the harness version's prompt. Output is schema-validated: Orient summary; proposals (demand, admit, re-weight, mutate request, remove, widen universe, consult request, harness patch, no action with reason); memory candidates.
  3. Deterministic Decide: pool rules, consult triggers (C4), pre-checks.
  4. Act through owner routes.
  5. Learn references.
  The packet carries `harness_version` and all references. Agent failure stops the packet at `observing` with the reason; deterministic risk rules still run.
- Runs inside an existing deployed process; it adds no controller framework. It publishes a loop-controller record for `per_persona_ooda` and per-persona health (last packet, stage reached, failure reason).
- Security boundary of the July hold: no new privileged route, no credential change. It uses the existing adapter and persona service tokens. Anything credential-related is a separate task.

### 6.2 Persona agent per trading persona (C2)

The provisioning saga calls the existing reconciler (`integrations/openclaw/persona_agent_sync.py`) for every persona, not only the Agora servant. Existing personas are backfilled.

The agent has:
- a read-only tool profile plus the proposal-submission tools of C1;
- SOUL from registry fields and the active harness version;
- MEMORY.md materialised from the memory service at sync and before each cycle turn.

One renderer is shared by the library and the ops script.

### 6.3 Memory writers and readers (C3)

- Configure the memory service's governance authorization in compose so retrieval works.
- Run `services/persona/learning_feedback_bridge.py` as a deployed worker.
- Deliver the research-finding outbox to the deployed memory address with a service credential. The sponsor is the persona that owns the demand or the pool member, not the fixed `persona-tw-equity`.
- Register every reflection lesson candidate in the memory service. Endorsement follows D5; merge follows endorsement.
- New writer identities and event types:
  - pool outcomes (admit, remove, re-weight, with reasons);
  - harness experiences (`harness_experience`: cue, lesson, evidence, confidence, outcome helped / hurt / no_effect / confounded);
  - consult outcomes.
- Readers: cycle start, demand writing, consult participants (committee-visible scope only), evolution requests. Private memory never crosses personas.

### 6.4 Consult policy and persona panel (C4)

- Trigger engine as D6.
- The persona service calls the consultation service as a service client with `from_persona_id`. The executor fans out one structured, deny-all, advisory call per participant persona through that persona's own agent, then one synthesis. This keeps the 2026-10-18 single-call contract per participant.
- Outputs:
  - ConsultMemo, as today;
  - an evidence reference on the decision it gates;
  - consult-outcome memory to the initiator;
  - an institutional cross-persona observation.
- D7 veto. Provisioning of the two personas follows D8.

### 6.5 Consumers for approved conversation measures (C5)

D9. Each consumer calls the owner's existing route. An approval that has no consumer is refused at approval time rather than stored silently.

### 6.6 ①② sync: demand drives the existing persona strategy discovery (C6)

- The cycle's research demand (`research_demand.v1`, delivered by `PERSONA-AUTONOMY-CYCLE-20261112`) runs the existing discovery scorer for that persona, filtered by the demand's missing families and datasets. Tried families and negative memory are excluded.
- The match's recommended actions are executed through the existing owners and recorded on the demand:
  - `create_research_ticket` → research tickets;
  - `promote_seed_candidate` → seed store and L2;
  - `run_rapid_eval`;
  - `request_data_backfill` → L1 desired state.
- The operator route stays. The collector query plan, L2 priority and template search tasks keep reading the demand unchanged.

### 6.7 Restored Agora learning surfaces (C7)

| Surface | Owner behind it |
| --- | --- |
| committee sessions and memos | consultation service (panel requests and memos) |
| memory review list and quarantine | memory service (entries and lesson candidates) |
| persona lab: draft, evaluate, submit-commit | persona owner harness patches (C8); the `trainer_feedback_to_persona_update` handoff is consumed by the persona management review |
| skill coaching | the same pipeline for patch type `skill` |
| evaluation suites and runs | shadow evaluation records (C8) |
| signal feedback, notes, insights, training examples | Agora write service → evidence inbox → L5 dataset → L6; referenced by C1 Observe |
| change log with Accept / Undo / Never again / Tell AI why | harness version history: undo = rollback patch; never again = negative experience |
| shadow comparison view | C8 arms |

The BFF only forwards and composes; the front end lives in `ajoe734/execute-plans`.

### 6.8 Harness self-optimisation (C8)

- **Mutable surface, versioned as `persona_harness.v1`:**
  - identity and mandate text inputs;
  - guardrails (forbidden solo actions, allowed tools) — tightening only inside paper;
  - consult policy;
  - tool guidance;
  - skills;
  - workflow cadence;
  - cycle thresholds;
  - memory retrieval policy.

  Outside the surface: risk and capital limits, environment ceiling, execution authority, live permissions.
- **Proposal:** a PersonaPatch (SD-02, with added types `skill`, `workflow`, `threshold`, `memory_policy`), raised by the Learn stage or by a human in the persona lab. Each change must cite trajectory evidence: packet ids and moments, virtual-book windows, memos, lessons.
- **Pre-check (static):**
  - the schema is valid;
  - the patch is not more permissive than the base;
  - no new tool permission, environment or authority;
  - no removed consult trigger for a high-risk action;
  - skills have a trigger line and existing referenced scripts;
  - always-on text stays within budget.
- **Shadow comparison:** replay recent frozen Observe bundles through the base and candidate harness with deny-all turns and no side effects. Compare proposal validity at the deterministic gates, consult outcomes and, where decisions differ and are measurable, virtual-book outcomes. `KEEP_BASE` is the default unless the candidate is better.
- **Adoption:**
  - inside paper: automated gate plus risk-persona sign-off (D6, D7) → new active version;
  - outside paper: operator through governance (`mutate` route).
  - Limits: one active experiment per persona; cooldown 7 days per component; observation window 10 trading days.
- **Review:** at the end of the window the outcome is marked helped, hurt, no effect or confounded and written as a harness experience. A hurt outcome raises an automatic rollback proposal. Later proposals must read relevant experiences.

### 6.9 ⑩ original plan (C9)

- The deterministic mapping of the 2026-07-05 spec replaces the agent's choice for every recommendation type. Routing:
  - canary candidacy → `PAPER-CANARY-PROMOTION-PROPOSAL-20261112`, plus a panel memo as evidence;
  - increase research or tool access → harness patch (`tool_profile`), for which the paper cap applies;
  - require retraining → member retrain request through the pool member actions;
  - reduce capital → paper allocation;
  - freeze, suspend, retire → existing lifecycle proposals (operator).
- The promotion review readback shows the canary approval.

## 7. Changes to existing tasks

- `PERSONA-AUTONOMY-CYCLE-20261112` keeps its acceptance. It gains dependencies on C1, C3 and C4. A note adds: its decisions run inside the C1 controller and packet; pool outcome memory goes through the persona service writer of C3; the admission decision calls the C4 trigger engine.
- `PAPER-CANARY-PROMOTION-PROPOSAL-20261112` is unchanged; C9 is ordered after it.
- Pool, ranking, screen, intraday and demand-search tasks are unchanged.

## 8. Delivery

Every task depends, directly or through its chain, on this design's task. Every acceptance keeps the standing rules:
- the production entry point is exercised by an integration test;
- missing data fails closed with a recorded reason;
- no synthetic data, members, memory, memos or metrics in production code;
- paper only;
- hosted acceptance uses naturally occurring events.

| Wave | Task | Scope |
| --- | --- | --- |
| 0 | `SD-PERSONA-CYCLE-RECONNECT-20261112` | this document |
| 1 | `MEMORY-WRITEBACK-WIRING-20261112` | C3: authorization, learning bridge worker, research outbox delivery and sponsor |
| 1 | `TRADE-LESSON-CANDIDATE-REGISTER-20261112` | C3: reflection lessons into memory review; D5 endorsement and merge |
| 1 | `PERSONA-AGENT-PROVISION-20261112` | C2 |
| 1 | `PERSONA-HARNESS-PATCH-RECORD-20261112` | C8: record, versions, pre-check, rollback |
| 2 | `PERSONA-OODA-CONTROLLER-20261112` | C1 |
| 2 | `CONSULT-POLICY-PERSONA-PANEL-20261112` | C4, D8 |
| 2 | `RECOMMENDED-MEASURE-CONSUMERS-20261112` | C5 |
| 3 | `PERSONA-DISCOVERY-DEMAND-LINK-20261112` | C6 (after the cycle task delivers the demand record) |
| 3 | `PERSONA-HARNESS-SHADOW-EVAL-20261112` | C8: shadow comparison |
| 3 | `PERSONA-HARNESS-ADOPT-REVIEW-20261112` | C8: adoption, observation, experiences, document amendments |
| 3 | `PM12-RECOMMENDATION-ROUTING-20261112` | C9 |
| 4 | `AGORA-COMMITTEE-MEMORY-RESTORE-20261112` | C7: committee and memory review |
| 4 | `AGORA-PERSONA-LAB-RESTORE-20261112` | C7: persona lab, skill coaching, evaluation, change log, shadow view |
| 4 | `AGORA-LEARNING-EVIDENCE-RESTORE-20261112` | C7: feedback, notes, insights, training examples |
| 5 | `BFF-PERSONA-CYCLE-VIEWS-20261112` | Management reads for cycle, memory, memos, harness (D13) |
| 5 | `FE-AGORA-LEARNING-SURFACES-20261112` | execute-plans pages for C7 |
| 5 | `FE-PERSONA-CYCLE-VIEWS-20261112` | execute-plans persona cycle views |
| 6 | `PERSONA-CYCLE-RECONNECT-HOSTED-ACCEPTANCE-20261112` | operator-side acceptance on hosted dev |

Document amendments are delivered inside the tasks that need them:
- `EVOLUTION_REVIEW_AND_THRESHOLDS.md` (paper harness mutate; persona cycle as requester through the controller);
- `services/memory/MEMORY_LAYER_DESIGN_NOTE.md` (writers, `harness_experience`);
- `PERSONA_TRADE_JOURNAL_GAP.md` (D5);
- `docs/02-architecture/phase4-bff-thinning.md` (restored surfaces);
- `docs/deployment/loop-catalog.registry.json` (overlay controller);
- a new `CONSULTATION_SESSION_PROTOCOL.md` (D7, the follow-up named by `OPENCLAW_RUNTIME_CONTRACT.md:516`).

## 9. Risks

| # | Risk | Control |
| --- | --- | --- |
| R1 | Model cost and quota (one turn per persona per day plus panels and shadows) | daily cadence; shadow replays bounded per experiment; per-persona budget recorded in the packet |
| R2 | Memory poisoning by the agent | agent writes candidates only; owners write; D5 gates; quarantine surface |
| R3 | The harness drifts or games its own evaluation | not-more-permissive pre-check; immutable risk surface; KEEP_BASE default; one experiment at a time; rollback on hurt; risk-persona veto |
| R4 | The panel rubber-stamps (same model family) | independent first round; a different provider or model per role where the pool allows; disagreements kept; operator sees the vetoes |
| R5 | The July security concerns return | no new privileged route or credential change in C1; credentials stay a separate task |
| R6 | Shared files with the pool tasks | ordering in §7 and the dependencies in §8 |
| R7 | Deterministic risk rules depend on the agent | they run outside the agent turn (D2) |

## 10. Sources

All repository paths are relative to the repository root. The original planning was recovered on 2026-11-12 from the dev tree at a7ff97105 by ten readers with one adversarial verifier each and a final document sweep. Hosted facts are read-only observations from the same day.
