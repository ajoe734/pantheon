# SD-PERSONA-MEETINGS-20261115 — persona meetings beyond gate reviews: evolution direction, optimisation, new strategies, post-mortem, regime, quarterly review

- Status: coordinator draft (d668c636a, base `f5e860882`), citations re-verified by Claude on `137ebeb20`; §4 licence/activity re-check via GitHub API was not performed (no network verification in this pass) and stays *(unverified)*; §9 left open. Original base `f5e860882` (origin/dev, 2026-11-15). Design only; `hosted_behavior_claimed: false`.
- Every numeric value is a coordinator proposal unless a citation says otherwise.
- Citation verification (Claude, base `137ebeb20`): line numbers in §2 and §3 re-resolved against the delivery base; corrections applied in place. Items not re-opened are marked *(unverified)* where noted.
- Rows marked **plan** restate the original plan; rows marked **added** are coordinator additions filling what the plan left open.

## 0. 中文摘要（操作者用）

**為什麼有這份：** 操作者 11/15 指出，會議不該只有「審查晉級」，要訂出各種功能，讓不同 persona 討論出演進方向、優化與新策略。目前程式只有四種「審查型會議」（進池試用、池子發布、行為修改、晉升真錢候選），每次只有發起者加風控（必要時加紅隊），一輪、彼此看不到答案；persona 不能自己召集討論，其他交易 persona 不會出席，會後只有發起者記錄。

**這份訂的事：**
1. 會議分兩類：**審查型**（現有四種，規則不變：一輪、各自獨立、風控可否決）與**討論型**（新增）。
2. 討論型六種：池子演進會議、優化會議、新策略發想會議、事後檢討會議、盤勢研判會議、季度檢討會議。每種寫明何時開、誰召集、誰出席、看什麼資料、產出交給哪個既有的正式流程。
3. 討論型的開法：會前各自寫解讀 → 第一輪各自獨立表態（附信心）→ 第二輪看得到別人（匿名）的立場與理由後回應或修正 → 召集人寫結論與提案；紅隊固定唱反調；不投票、異議留紀錄；最多兩輪，召集人可因新證據加開第三輪。
4. 會議本身不執行任何事。提案一律走既有入口（研究需求單、演化提案、行為修改、教訓候選、池子決策、治理提案），後面的驗證與審查型會議照舊。
5. 會議紀錄（誰說了什麼、提案）是共享事實；每位出席者把自己會後的解讀寫進自己的私人記憶（現在只有發起者會寫）。
6. 不新增排程：週會掛在 persona 的每日循環上，事件型會議由既有事件觸發；persona 也可以在每日循環提出「要開會」。
7. 每種會議有出席人數、輪數、每週次數與每次呼叫上限。

**要操作者決定的：** 見 §9。

## 1. Operator statements (verbatim)

Transcript `/home/chloe_ong_dev_cctech_support_com/.claude/projects/-home-chloe-ong-dev-cctech-support-com-code-pantheon/f4c32790-8a11-4908-abda-046d0a900ad2.jsonl`:

| Line | Time (UTC) | uuid | Statement |
|---|---|---|---|
| 9354 | 2026-11-15T11:20:10.508Z | ebb575da-43ce-483e-9c1f-f1fcf6ae684e | 「會議你應該要訂出各種功能，例如除了審查晉級以外，然後讓不同persona討論出演進方向或是優化或是新策略」 |

Verification note: the coordinator draft also cited line 9543 (uuid 8d0704fd-…); that line is a tool result, not an operator statement, and is removed. The statement above is the second sentence of a longer message; the first part of the same message asks for the memory-tool, Hindsight and hosted-retrieval items, which belong to other tasks.

Earlier operator rules that bind this design (transcript `cb4f6795-9064-4bb2-ab77-c0740122bce1.jsonl`, cited in `MEMORY-HINDSIGHT-HOSTED-ACCEPTANCE-20261113`): line 2535 (uuid 310aefa2-9b3c-4eba-a4d2-d510f4d37f60) private memory never crosses personas, sharing happens in meetings with full content; line 2662 (uuid ef676e94-9562-403c-99fd-03e8a1db5db2) facts are shared, memory is each persona's interpretation, meetings are where personas learn how their interpretations of the same fact differ.

## 2. What the original plan defines

| Item | Citation | State today |
|---|---|---|
| Seven-stage loop 討論→建議→模擬→決策→執行→回顧→學習; Stage 1 is persona/operator/committee discussion; Stage 2 a Sponsor Persona certifies; Stage 4 signed by operator or reviewer persona | `docs/04/pantheon_evolution_generative_loop_gap_2026-07-14/SEVEN_STAGE_DISCUSSION_LOOP_SPEC.md:41-89` | only through the committee sponsor-decision adapter (`services/consultation/main.py:1076` (route decorator)) |
| Agora Workshop adapter: debate transcript plus consensus → evolution proposal | same spec `:136-143` | never built |
| Persona as requester, responder, committee participant | `PERSONA_RUNTIME_MODEL.md` §13 (`:293-301` area) | a persona can only request its own gated panel; responders are the risk and red-team personas |
| Consult Policy: required reviewers, trigger rules (risk before paper, macro analyst on regime shift), forbidden solo actions | `PERSONA_RUNTIME_MODEL.md` §6 (`:165-179` area); `Pantheon_總索引版系統分析文件.md:781-785` | one global policy `consult-policy-v1` (`services/persona/consult_triggers.py:29-48`); no regime rule |
| Session types consult / committee / red_team | `services/control-plane/persona/contract.md:138-142` | enum only |
| SD-05 request types strategy_review, redteam, data_leakage, execution_risk, capital_pool, incident, persona_policy; CommitteePolicy; memo type `dissent` | `docs/03/SD-05_consultation_redteam.md` (`CommitteePolicy` at `:121`, `RedTeamScenario` at `:135`, memo type `dissent` at `:154`; request-type list and `:294-319` *(unverified)*) | requests and memos exist; CommitteePolicy and RedTeamScenario absent |
| Multi-persona conflict → committee override | `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md` §6.3 (`:215-227`) | `services/governance/multi_persona/` has no production caller (only string ids in `services/persona/agent_usability_validation.py:177-178` and docs refs) |
| Committee voting and quorum deferred | `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md:213` | still deferred; the reconnect design D7 chose "no vote" |
| Escalation to committee review during cooldown | `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md` §5.4 (`:151-154`) | flag only (`services/control-plane/governance/evolution_controller.py:323-324`, note `rollback_escalation`) |
| Persona panel D6/D7: initiator + risk (+ red team), independent answers, one synthesis, no vote, risk oppose → operator | `SD_PERSONA_CYCLE_RECONNECT_2026-11-12.md:181-182,251-260`; `CONSULTATION_SESSION_PROTOCOL.md` | implemented for `trial_admission`, `envelope_publication`, `harness_patch`; canary candidacy (D12) through the evaluator |
| Red team on a gate pass entering a pool; risk persona on member removal or replacement | `SD_PERSONA_CYCLE_RECONNECT_2026-11-12.md:205,209` | never built |
| H10 pre-panel interpretation turn; each participant writes its own outcome | `SD_MEMORY_HINDSIGHT_ADOPTION_2026-11-13.md:21,66-67,152` | task `PANEL-FULL-INTERPRETATIONS-R2-20261114` (todo) |
| Rubber-stamp control: independent first round, different provider or model per role | `SD_PERSONA_CYCLE_RECONNECT_2026-11-12.md:383` (R4) | no per-role model selection in the panel code |
| Operator interaction modes and pickers (risk committee, red team, same-archetype, cross-style) | `docs/product/persona-interaction-and-governed-action-plan.md` §5.2-5.3 (`:135-166`) | Agora interactions, operator-initiated only (`services/control-plane/bff/agora/interaction/router.py:777`) |

**Unreconciled conflict in the plan.** The operator rule (§1) says personas learn in meetings how each other's interpretations differ, but `CONSULTATION_SESSION_PROTOCOL.md` §3 says a participant never sees another participant's answer. §5 M3 resolves this: gate panels keep independence; deliberative meetings add a second round with cross-visibility.

## 3. Verified current state (base `f5e860882`)

1. Only three decision types open a panel (`GATED_DECISION_TYPES`, `services/persona/consult_triggers.py:43-45` (set) and `:121-136` (`required_consultation` returns `None` for other types)); any other type is refused with 422 (`services/consultation/main.py:168`).
2. The daily cycle's `consult_request` proposal is only tracked against an already recorded harness patch (`services/persona/harness_cycle.py:178-185`). A persona cannot call a meeting about direction or strategy.
3. The roster is fixed: initiator, `persona-risk-officer`, `persona-red-team`. Peer trading personas never attend.
4. One round, no cross-visibility (`_panel_contribution` at `services/consultation/workflow_executor.py:936`); synthesis is deterministic grouping (`_synthesize` at `:987`).
5. Memory writeback goes only to the initiator (`_write_panel_memory`, `workflow_executor.py:1065`; `contributing_persona_ids: [initiator]` at `:1101`) plus one institutional observation.
6. The Agora committee board (`services/control-plane/bff/agora/committee/router.py:16-36`; sessions list and read only) lists panel requests; no route creates a session.
7. Governed outputs that a meeting can feed already exist:

| Output | Entry point |
|---|---|
| Research demand | `POST /api/research/persona-demands` (`services/research/main.py:3011`) |
| Research ticket | `POST /api/research/tickets` (`services/research/main.py:4863`) |
| Evolution proposal | `POST /api/evolution/proposals` (`services/evolution/main.py:1539`) via the sponsor decision bridge |
| Harness patch | `POST /api/personas/{id}/harness/patches` → precheck → shadow → adoption (`services/persona/harness.py:528-561`) |
| Lesson candidate | `POST /api/memory/trade-lessons` → `/risk-review` → `/paper-endorse` (`services/memory/main.py:868,1055,1130`) |
| Lesson → rule | `POST /api/personas/{id}/lesson-rule-proposals` (`services/persona/lesson_rule_proposals.py:365`) |
| Pool action | `POST /api/personas/{id}/strategy-pool/decisions` (`services/control-plane/persona/main.py:854`) |
| Governance proposal | `POST /api/governance/approvals` (`services/governance/main.py:1631`) |
| Memory | `POST /api/memory/writebacks/learn-feedback` (`services/memory/main.py:525`) |

## 4. Established practice (survey 2026-11-15; licences and activity from the GitHub API)

| Source | Format | Taken | Not taken |
|---|---|---|---|
| TradingAgents (Apache-2.0, active) | analysts in parallel; bull vs bear debate (default 1 round); research manager judges; risk debaters; portfolio manager decides; reflection after 5 trading days | judge rules: conflict alone is not a reason to hold; weigh sides independent of speaking order; unreadable output is `REVIEW`, never a default; deeper model for the judge | free debate without independent first round |
| HedgeAgents (WWW 2025, no code) | Budget Allocation Conference every 30 days; Experience Sharing Conference each cycle; Extreme Market Conference on a >5% day or >10% 3-day move | the three meeting shapes map to quarterly review, post-mortem and interpretation exchange | its merge weights (unverified) |
| FinCon (NeurIPS 2024, code unreleased) | manager over analysts; self-reflection on CVaR drop; compare good and bad episodes; belief updates sent only to concerned agents | post-mortem compares good and bad episodes; outputs go only to concerned personas | prompt rewriting by a meta-agent (harness patches keep their own gate) |
| Du et al. multi-agent debate (ICML 2024) | independent answers, then read others and revise; 3 agents × 2 rounds | two rounds by default | — |
| MAD (EMNLP 2024) | affirmative/negative + judge; Degeneration-of-Thought | fixed devil's advocate (red team) | — |
| ReConcile (ACL 2024) | round table of different models with confidence | confidence on every position; mixed models | confidence-weighted vote as the decision |
| ChatEval | one-by-one beats simultaneous; identical role prompts ≈ one agent; more turns did not help | distinct roles; round cap | — |
| InfoDelphi (2026 preprint) | identical evidence → herding; split shared and private evidence | private interpretation turn before round 1 | — |
| Conformity and sycophancy studies (ICLR 2025, ACL 2025, Choi et al.) | conformity grows with longer interaction and majorities; anonymising reduces sycophancy; voting explains most debate gains | anonymised round 2; short meetings; meetings propose, governed gates decide | debate as a decision mechanism |
| Concordia (DeepMind, Apache-2.0) | shared world, private agent memories | matches the operator's facts/interpretations rule | — |

No framework is adopted as a dependency: the meetings run inside the existing consultation service and OpenClaw agents.

## 5. Decisions

| # | Decision | Basis |
|---|---|---|
| M1 | **Two classes.** Gate panels (trial admission, envelope publication, harness patch, canary candidacy) keep `CONSULTATION_SESSION_PROTOCOL.md` unchanged: one independent round, risk veto. Deliberative meetings are a new consultation type `persona_meeting` with its own protocol (M3). A deliberative meeting never gates anything. | plan D7 kept; **added** class split |
| M2 | **Catalogue.** Six meeting types, §6. | **added**, shapes from §4 |
| M3 | **Protocol.** (a) Pre-read: shared case file of facts plus each participant's own interpretation turn per H10 (own agent, `scope:committee` recall, full text, ≤12 citations); until H10 is delivered, committee-visible entries in full as H10 AC4 already specifies. (b) Round 1: each participant writes a position independently: stance on each agenda item, proposals, confidence 0–1, citations. (c) Round 2: each participant sees every round-1 position in full but with participants labelled by role and style, not by name, and answers: what it accepts, what it disputes and why, whether its own position changed and why. The red-team persona, when present, is assigned the opposing case to the leading proposal. (d) A third round only when the chair records new evidence that arrived during the meeting. (e) Synthesis by the chair (M4). | plan Stage 1–2; **added** rounds, anonymisation, confidence, devil's advocate (§4) |
| M4 | **Decision rule.** No vote (plan deferral, D7). The chair is the sponsor persona: the owning persona for its pool meetings, a rotating trading persona for cross-persona meetings. The chair writes the outcome: proposals, each with sponsor, citations and the positions for and against; a confidence-weighted tally is reported but not binding; dissent is recorded as a `dissent` memo (SD-05 memo type). Judge rules from TradingAgents apply. An unreadable or empty synthesis is `REVIEW` and produces no proposal. | plan Stage 2 sponsor; SD-05 `dissent`; **added** judge rules |
| M5 | **Outputs.** A meeting executes nothing. Every proposal goes through an existing entry point in §3.7 under its sponsor's persona identity, and every downstream gate still applies (a strategy idea still goes through validation and the trial-admission panel; a harness proposal through pre-check, shadow and the harness panel). | plan "agent reads and proposes, owner executes"; `phase4-bff-thinning.md` rule 3 |
| M6 | **Memory.** The minutes (agenda, positions per round, proposals, dissent) are shared facts: one institutional entry. Each participant's own outcome interpretation is written to its own private bank by the consultation service as owner of the event (fixes §3.5). Interpretations cross personas only through the meeting record. | operator rules §1; `MEMORY_LAYER_DESIGN_NOTE.md` owner-writes |
| M7 | **Participants and models.** Participants answer from their own consult agents (deny-all in rounds; the interpretation turn uses the H9 broker restrictions). Where the model pool allows, the risk and red-team personas run on a different provider from the trading personas. | plan R4 (`SD_PERSONA_CYCLE_RECONNECT_2026-11-12.md:383`); ReConcile |
| M8 | **Triggers without a new scheduler.** Weekly meetings are convened by the persona's daily cycle on its weekly evolution day; cross-persona weekly meetings by the first enrolled persona's cycle of the week (idempotent per ISO week); event meetings from existing events (§6). A persona can request any deliberative meeting through its daily-cycle `consult_request` proposal; the request is honoured within the budget (M9). | **added**; reconnect design "no new scheduler" |
| M9 | **Budgets.** At most 5 participants; at most 2 rounds plus the evidence round; at most 3 deliberative meetings per persona per ISO week; one call per participant per round plus one interpretation turn and one synthesis; refused when the meeting budget for the day is spent, recorded with a reason. | **added** (cost risk R1 of the reconnect design) |
| M10 | **Visibility for operators.** The Agora committee board lists both classes, every round and the proposals with links to their downstream status. Operators can convene a deliberative meeting with a topic through the same consultation route; they read, they do not take part in rounds. | plan D11, `persona-interaction-and-governed-action-plan.md` |
| M11 | **Missed gate rules from the reconnect design** (red team at a gate pass entering a pool; risk persona at member removal or replacement) stay out of this design; they are gate-panel extensions. | `SD_PERSONA_CYCLE_RECONNECT_2026-11-12.md:205,209` |

## 6. Meeting catalogue

| Type | Purpose | When | Chair / sponsor | Participants | Inputs (case file) | Outputs (§3.7) |
|---|---|---|---|---|---|---|
| MT-1 池子演進會議 pool evolution review | decide where the pool should evolve: which members to mutate, retrain, replace, or which gap to research | weekly per enrolled persona on its weekly evolution day; also when pool health reports demand or a decay alarm fires | owning persona | owner + 1 peer trading persona of a different style (rotating) + risk persona | pool health, member virtual books, decay and revalidation results, trial counterfactuals, open research demands, owner interpretation | evolution proposals (mutate, retrain, remove member) via pool decisions and evolution proposals; research demands |
| MT-2 優化會議 optimisation review | improve how the persona and its members act | when ≥3 lesson candidates await review for the persona, a harness patch candidate exists, or an expression-layer counterfactual window (20 trading days) completes | owning persona | owner + peers holding the same strategy family + risk persona | pending lessons with their trades, harness patch candidates with shadow results, expression counterfactuals, memory usefulness outcomes | harness patch proposals; lesson-to-rule proposals; expression parameter proposals through evolution |
| MT-3 新策略發想會議 strategy ideation | turn research findings into testable strategy ideas and assign them | weekly, cross-persona; also when ≥5 new hypothesis cards or template passes arrive since the last meeting | rotating trading persona | all enrolled trading personas (≤4) + red-team persona as devil's advocate | new hypothesis cards, template-search passes, research findings, knowledge-base retrieval, each pool's open demands | research demands each sponsored by one persona; endorsement of a hypothesis card for validation; an idea may be sponsored by any persona, not only the one that found it |
| MT-4 事後檢討會議 post-mortem | learn from a loss | a member removed at the 10% drawdown limit; a persona drawdown breach; an incident with paper loss | affected persona | affected persona + risk persona + red-team persona (+ peers holding the same family) | trade journal, the decisions and memories cited before the loss, usefulness outcomes, a comparable profitable episode (FinCon) | lesson candidates to the risk-review path; negative-memory family tags; harness patch proposals; sent only to the concerned personas |
| MT-5 盤勢研判會議 regime interpretation | exchange interpretations of a regime change | a governed RegimeState transition on the macro axis, or on the primary axis of any enrolled pool (after `SD-REGIME-STATE-ENGINE-20261114` consumers) | rotating trading persona | all enrolled trading personas + risk persona | the regime records and market-state measurement, each pool's exposure | each persona's interpretation into its private memory; optional reweight proposals or research demands; minority view kept |
| MT-6 季度檢討會議 quarterly review | review the quarter before allocation and promotion | quarter end, before the paper allocation run | rotating trading persona | all enrolled trading personas + risk + red team | rankings, pool health, written self-assessment and budget request from each persona (HedgeAgents) | memo attached as evidence to the allocation run and to any canary candidacy (both keep their own gates); research priorities for the next quarter |

## 7. Target flow

```
trigger (daily cycle / event / persona consult_request / operator)
   └─► consultation persona_meeting (type, agenda, roster, budget check)
          ├─ pre-read: shared facts case file + own interpretation turn per participant
          ├─ round 1: independent positions (confidence, citations)
          ├─ round 2: anonymised cross-visibility, accept/dispute/change
          ├─ (round 3 only on new evidence)
          └─ chair synthesis: proposals + tally + dissent  ──► minutes (institutional fact)
                                                         ├──► each participant's own outcome memory
                                                         └──► proposals → existing entry points (§3.7) → existing gates
```

## 8. Delivery

| Task | Scope | Depends on |
|---|---|---|
| MEETING-PROTOCOL-ENGINE | consultation type `persona_meeting`: types, rosters, budgets, rounds, anonymised round 2, synthesis with proposals and dissent memo, per-participant writeback; `CONSULTATION_SESSION_PROTOCOL.md` gains the deliberative section | this design |
| MEETING-TRIGGERS | daily-cycle convening of MT-1/MT-2/MT-3; MT-4 from removal, drawdown and incident events; MT-6 from the quarter-end allocation run; `consult_request` proposals open meetings | engine |
| MEETING-OUTPUT-ROUTING | proposals to the §3.7 entry points under the sponsor's identity; downstream status readback | engine |
| MEETING-REGIME-TRIGGER | MT-5 from RegimeState transitions | engine, `REGIME-CONSUMERS-FIRST-WAVE-20261115` |
| BFF and FE meeting views | Agora committee board shows both classes, rounds, proposals, downstream status; operator convening | engine |
| MEETING-HOSTED-ACCEPTANCE | natural meetings on hosted with real personas | all, plus enrolled personas |

The H10 interpretation turn (`PANEL-FULL-INTERPRETATIONS-R2-20261114`) is reused when delivered; until then the engine uses committee-visible entries in full.

## 9. Operator decisions requested

1. The six meeting types and their cadence (§6).
2. Cross-visibility in deliberative meetings (round 2, anonymised) while gate panels stay independent (M1, M3).
3. Peer trading personas attend another persona's MT-1/MT-2; the owning persona still sponsors (M4).
4. No vote; the chair sponsors and dissent is recorded (M4).

## 10. Risks

| # | Risk | Handling |
|---|---|---|
| K1 | Model cost | M9 budgets; weekly cadence; refusals recorded |
| K2 | Herding and sycophancy | private interpretation first; independent round 1; anonymised round 2; devil's advocate; short meetings; mixed models |
| K3 | Meetings bypass gates | M5: proposals only through existing entry points and gates |
| K4 | Private memory leaks across personas | interpretation turn limited to `scope:committee`; rounds deny-all; only minutes are shared |
| K5 | Too few enrolled personas | cross-persona meetings need at least 2 trading personas; otherwise recorded as `insufficient_participants` |
