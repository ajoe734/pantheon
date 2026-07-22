# Loop Remediation Sequencing & Execution Matrix (2026-07-16)

Status: Authoritative sequencing mapping implementing the re-sequencing mandated by `docs/04/pantheon_loop_product_level_remediation_2026-07-13/REMEDIATION_SEQUENCING_ADDENDUM_2026-07-16.md`.

This matrix maps all 48 catalog tasks to their updated execution waves and dependency lists. It decouples the security/governance tasks (G1/attestation) from the functional loops, ensuring that a complete end-to-end permissive paper-trade loop (G2) is achieved and verified before opening the strict Hardening Wave.

---

## 1. High-Level Wave Structure

| Execution Phase | Waves | Gating Policy & Posture |
|---|---|---|
| **Phase A: Permissive Paper-Trade Closeout** | Wave 0 — Wave 4 | Runs under permissive auth (`AUTH_MODE=permissive`, `AUTH_STUB=true`). Security and attestation dependencies are removed to allow functional verification of signal-to-telemetry loops. Ends at `LOOP-PROD-CLOSE-001`. |
| **Phase B: Strict Hardening Wave** | Wave 5 — Wave 7 | Opens ONLY after G2 paper-trade loop is proven. Re-enables strict auth, MFA, browser cutover, attestation roots, and final Human/Ops sign-off. Ends at `LOOP-PROD-CLOSE-002`. |

---

## 2. Detailed Task Matrix

### Wave 0 — Functional Substrate (Permissive Auth)

#### LOOP-PROD-000: Canonical loop inventory and OODA overlay truth

- **Summary**: 校正 loop catalog、BFF inventory 與 verification index：維持 12 個 L1 canonical loops，新增 per_persona_ooda 為 composite_overlay 並宣告 composed_of；archived task 不得被投影成 live maturity。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Define the 12 loops and composite OODA overlay.
- **Original Dependencies**: None
- **Amended Dependencies**: None

#### LOOP-PROD-001: Durable controller truth substrate

- **Summary**: 建立 tenant/environment scoped 的 durable controller record store、writer SDK 與 projector，記錄 desired/actual query、lease、dedupe、heartbeat/tick/success/failure/repair、backlog、deployment SHA 與 evidence truth level。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Set up tenant/env-scoped controller records.
- **Original Dependencies**: `LOOP-PROD-000`
- **Amended Dependencies**: `LOOP-PROD-000`

#### LOOP-PROD-002: Product evidence schema and anti-false-close gate

- **Summary**: 建立 machine-readable product evidence schema 與 supervisor closeout guard，拒絕 phantom cross-repo delivery、mock-only live claim、缺 terminal readback/restart/hosted/security/reviewer 或 unsupported maturity。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Define structured evidence schema rules.
- **Original Dependencies**: `LOOP-PROD-000`, `LOOP-PROD-001`
- **Amended Dependencies**: `LOOP-PROD-000`, `LOOP-PROD-001`

#### LOOP-PROD-REC-001: Full-stack loop recovery and fault-injection harness

- **Summary**: 建立可重複的 target-dev recovery harness，在 outbox、downstream mutation、receipt、projection 各切點注入故障，並驗證 duplicate、lease expiry、timeout、worker/BFF/DB/full-stack restart。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Set up full-stack restart/recovery & fault injection.
- **Original Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`
- **Amended Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`

---

### Wave 1 — Canonical Loops & Side Effects (Permissive Auth)

#### LOOP-PROD-AGORA-001: Durable Agora evidence, dataset, and handoff worker

- **Summary**: 將 interaction、feedback、note、journal、insight 事件送入 tenant-scoped durable inbox，由預設 worker 產生 versioned dataset 與 evidence handoff；只可供 Observe/Learn，不得直接 deploy/trade。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Agora handoff and dataset worker.
- **Original Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `LOOP-PROD-TEACH-001`, `LOOP-PROD-AUTH-001`, `AG-GAP-014`
- **Amended Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `LOOP-PROD-TEACH-001`, `AG-GAP-014`

#### LOOP-PROD-AGORA-002: Implement six deferred Strategy Workshop operations

- **Summary**: 實作 v1.5 六個目前故意 501 的 operations：GET/POST versions、select version、POST research-runs、POST consultations、POST conclude；全部走 canonical store/command 並更新 OpenAPI/bundle/compat manifest。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Implement deferred workshop operations.
- **Original Dependencies**: `LOOP-PROD-CONS-001`, `LOOP-PROD-ALPHA-001`, `AG-GAP-005`, `LOOP-PROD-ATTEST-001`
- **Amended Dependencies**: `LOOP-PROD-CONS-001`, `LOOP-PROD-ALPHA-001`, `AG-GAP-005`

#### LOOP-PROD-AGORA-003: Hosted Strategy Workshop generated client and actions

- **Summary**: 在 execute-plans 更新 exact contract digest/generated client，完成六個 Strategy Workshop actions 的 terminal receipt/refetch、strict-auth desktop/mobile、degraded/error 與 compatibility gate。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Expose workshop controls on execute-plans.
- **Original Dependencies**: `LOOP-PROD-AGORA-002`, `LOOP-PROD-FE-001`, `AG-GAP-013`, `AG-GAP-014`, `OPS-EP-DEV-MAIN-RECONCILE-001`
- **Amended Dependencies**: `LOOP-PROD-AGORA-002`, `AG-GAP-013`, `AG-GAP-014`, `OPS-EP-DEV-MAIN-RECONCILE-001`

#### LOOP-PROD-ALPHA-001: Durable Alpha Replication and revalidation worker

- **Summary**: 讓 reviewed immutable StrategySpec 進 durable replication queue，由預設 scheduled/command worker 執行非 stub revalidation，寫入真實 ExperimentRun 與 evidence lineage；production activation 保持 gate-closed。
- **Classification**: part of the G2 proof path
- **Rationale**: Strategy replication processor.
- **Original Dependencies**: `LOOP-PROD-DIST-001`
- **Amended Dependencies**: `LOOP-PROD-DIST-001`

#### LOOP-PROD-BFF-001: Authoritative BFF health monitoring and loop-health projection

- **Summary**: BFF monitor 使用各 downstream 真實 readiness contract，telemetry 具 canonical identities 並持久化 incident/recovery；/bff/v5/loop-health 讀 controller snapshots，不再 registry-only，清楚區分 stale/snapshot/scheduled/reconciled/live。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Exposes loop metrics & health.
- **Original Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-TEL-001`, `LOOP-PROD-AUTH-001`, `LOOP-PROD-REC-001`, `LOOP-PROD-EVO-001`
- **Amended Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-TEL-001`, `LOOP-PROD-REC-001`, `LOOP-PROD-EVO-001`

#### LOOP-PROD-CAP-001: First-class bounded paper DecisionSignalProducer

- **Summary**: 建立非 SmokeStrategy/手工 script 的 first-class DecisionSignalProducer，發現 eligible paper bindings，攜帶 tenant/persona/binding/runtime/pool identities，確保 exactly-one worker、queue isolation、stop/restart，live broker/capital fail closed。
- **Classification**: part of the G2 proof path
- **Rationale**: Bounded paper order placement.
- **Original Dependencies**: `LOOP-PROD-DEP-001`, `LOOP-PROD-REC-001`, `TJ-E2E-014`
- **Amended Dependencies**: `LOOP-PROD-DEP-001`, `LOOP-PROD-REC-001`, `TJ-E2E-014`

#### LOOP-PROD-CONS-001: Real-participant Consultation workflow

- **Summary**: 移除自動製造 committee/memo/recommendation 的成功路徑；只有合格真實 participant/provider-authored transcript、memo 與 review evidence 才能 publish/handoff，provider 不可用時必須 waiting/blocked。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Reconcile participant consultation steps.
- **Original Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `LOOP-PROD-AGORA-001`
- **Amended Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `LOOP-PROD-AGORA-001`

#### LOOP-PROD-DEP-001: Canonical deployment dispatcher and RuntimeBinding readback

- **Summary**: 讓 deployment outbox worker 真正呼叫 runtime_manager_dispatch_adapter/canonical runtime authority，而非 receipt-only；成功前讀回 RuntimeBinding/post-state，支援 retry/DLQ/replay/restart/compensation/kill-wins。
- **Classification**: part of the G2 proof path
- **Rationale**: Core dispatcher with RuntimeBinding.
- **Original Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `LOOP-PROD-IMIT-001`
- **Amended Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `LOOP-PROD-IMIT-001`

#### LOOP-PROD-DIST-001: Durable Strategy Distillation event consumer

- **Summary**: 以 durable SourceRecord inbox/outbox 與預設 worker 擁有 distillation，僅更新 mutable StrategySpec draft head；支援 catch-up、out-of-order、duplicate、DLQ、restart 並保護 approved immutable artifact。
- **Classification**: part of the G2 proof path
- **Rationale**: Distillation queue handler.
- **Original Dependencies**: `LOOP-PROD-SRC-001`
- **Amended Dependencies**: `LOOP-PROD-SRC-001`

#### LOOP-PROD-EVO-001: Real Evolution target-plane dispatcher

- **Summary**: approved EvolutionDecision 必須呼叫 canonical governance/deployment/runtime target plane，不得 synthetic SUBMITTED；預設 worker 持久化 terminal receipt、target post-state 與 formal journal link。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Triggers evolution rounds.
- **Original Dependencies**: `EVOCHAIN-011`, `LOOP-PROD-DEP-001`, `LOOP-PROD-TEL-001`, `LOOP-PROD-REC-001`, `LOOP-PROD-TEL-002`
- **Amended Dependencies**: `EVOCHAIN-011`, `LOOP-PROD-DEP-001`, `LOOP-PROD-TEL-001`, `LOOP-PROD-REC-001`, `LOOP-PROD-TEL-002`

#### LOOP-PROD-IMIT-001: Default Human Imitation and shadow evaluation chain

- **Summary**: 讓 scheduler 自動發現合格 governed datasets，執行真實 shadow/OOS evaluator metrics，持久化 immutable candidate 與 lineage；不得靠 empty body，也不得繞過 experiment→approval→deployment。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Human imitation shadow evaluation algorithm.
- **Original Dependencies**: `LOOP-PROD-AGORA-001`, `LOOP-PROD-TEACH-001`, `LOOP-PROD-REC-001`, `LOOP-PROD-CONS-001`, `LOOP-PROD-AGORA-002`
- **Amended Dependencies**: `LOOP-PROD-AGORA-001`, `LOOP-PROD-TEACH-001`, `LOOP-PROD-REC-001`, `LOOP-PROD-CONS-001`, `LOOP-PROD-AGORA-002`

#### LOOP-PROD-OODA-001: Per-Persona OODA schedule reconciliation and product proof

- **Summary**: 在既有 cron→provider turn→packet 基礎上，將 eligible persona desired state reconcile 成 exact canonical schedules，修 missing/orphan jobs；一次 run 對應一次 real turn 與一個 packet/terminal failure，Act 只能 proposal。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Multi-persona reconciliation schedule.
- **Original Dependencies**: `LOOP-PROD-000`, `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `OPENCLAW-CRON-WRITE-SCOPE`, `OPENCLAW-PERSONA-CRON-BACKFILL`, `OPENCLAW-OODA-PACKET-CLOSURE`, `LOOP-PROD-BFF-001`
- **Amended Dependencies**: `LOOP-PROD-000`, `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `OPENCLAW-CRON-WRITE-SCOPE`, `OPENCLAW-PERSONA-CRON-BACKFILL`, `OPENCLAW-OODA-PACKET-CLOSURE`, `LOOP-PROD-BFF-001`

#### LOOP-PROD-SRC-001: Source requirement reconciler and default scheduler

- **Summary**: 把 persona/data requirement 當 desired state，預設 supervised reconciler/scheduler 持續建立與修復 connector、schedule，產生真實且有 provenance 的 normalized SourceRecord 與 SourceHealth。
- **Classification**: part of the G2 proof path
- **Rationale**: Permissive auth cutover enables default scheduler/reconciler.
- **Original Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `LOOP-PROD-AUTH-001`
- **Amended Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`

#### LOOP-PROD-TEACH-001: Fail-closed Persona Teaching on authoritative data

- **Summary**: 移除 STUB1/STUB2、stub-ref 與 unconditional passed proof；evaluation 讀 canonical versioned dataset、freshness、threshold policy，資料不足即 fail closed，preview worker 預設啟動。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Persona teaching processor on authoritative data.
- **Original Dependencies**: `LOOP-PROD-SRC-001`, `LOOP-PROD-REC-001`, `LOOP-PROD-ALPHA-001`
- **Amended Dependencies**: `LOOP-PROD-SRC-001`, `LOOP-PROD-REC-001`, `LOOP-PROD-ALPHA-001`

#### LOOP-PROD-TEL-001: Default telemetry reconciliation and incident chain

- **Summary**: 預設啟動 scheduler、consumer、incident listener，以真實 runtime/operator telemetry 比對 authoritative actual state，產生 DriftReport 與 dedup Incident；不可 empty-green/fixture，需 retry/DLQ/replay/restart/lag truth。
- **Classification**: part of the G2 proof path
- **Rationale**: Telemetry capture & incident pipeline.
- **Original Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `LOOP-PROD-CAP-001`
- **Amended Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `LOOP-PROD-CAP-001`

#### LOOP-PROD-TEL-002: Canonical loop-run and Trade Journey lifecycle projector

- **Summary**: 從真實 signal/decision/order/fill/position/reconciliation append events 投影 canonical loop-run 與 Trade Journey；維持單一 identity chain，manual/cron rebuild 只能標示 backfill，不能成為 live truth。
- **Classification**: part of the G2 proof path
- **Rationale**: Loop-run/Trade Journey lifecycle projection.
- **Original Dependencies**: `LOOP-PROD-CAP-001`, `LOOP-PROD-TEL-001`, `TJ-E2E-014`
- **Amended Dependencies**: `LOOP-PROD-CAP-001`, `LOOP-PROD-TEL-001`, `TJ-E2E-014`

---

### Wave 2 — Cross-Loop Paths & Verifiers (Permissive Auth)

#### LOOP-PROD-MAI-001: Hosted Management AI repair and dev-bridge backend proof

- **Summary**: 在 hosted strict auth 下證明 debug read-only 與 repair 完整鏈：activate→prepare narrow clean worktree→forward metadata→sentinel write/readback→SA/SD→pending packet→supervisor processed receipt→archive→deactivate。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Dev-bridge backend verification.
- **Original Dependencies**: `LOOP-PROD-AUTH-001`, `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `LOOP-PROD-TJ-001`, `LOOP-PROD-WORKER-001`, `LOOP-PROD-BROWSER-AUTH-001`
- **Amended Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `LOOP-PROD-TJ-001`

#### LOOP-PROD-MAI-002: Hosted Management AI repair product UI

- **Summary**: 在 execute-plans 呈現 mode/readiness、repair metadata、progress、receipt、deactivation與錯誤狀態；browser 不得直接連 OpenClaw，並證明 desktop/mobile/degraded/reconnect/rollback。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Management console UI for repair.
- **Original Dependencies**: `LOOP-PROD-MAI-001`, `LOOP-PROD-FE-001`, `MGMT-SSE-001`, `OPS-EP-DEV-MAIN-RECONCILE-001`, `LOOP-PROD-TJ-002`
- **Amended Dependencies**: `LOOP-PROD-MAI-001`, `MGMT-SSE-001`, `OPS-EP-DEV-MAIN-RECONCILE-001`, `LOOP-PROD-TJ-002`

#### LOOP-PROD-PER-001: Persona provisioning through binding and first-evaluation readback

- **Summary**: persona create 必須保持 provisioning，直到 canonical RuntimeBinding、paper worker 與 first evaluation schedule 全部 read back；使用真實 persona identity，不可 seed binding，失敗需 terminal/restart-safe。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Binding and evaluation loop setup.
- **Original Dependencies**: `LOOP-PROD-DEP-001`, `LOOP-PROD-CAP-001`, `LOOP-PROD-OODA-001`, `PPL-ALLOC-010`, `PPL-ALLOC-011`
- **Amended Dependencies**: `LOOP-PROD-DEP-001`, `LOOP-PROD-CAP-001`, `LOOP-PROD-OODA-001`, `PPL-ALLOC-010`, `PPL-ALLOC-011`

#### LOOP-PROD-TJ-001: Canonical Trade Journey governed action backend

- **Summary**: 取代 default ACTION_DISPATCH_UNAVAILABLE；pause/cancel/escalate/retry/ack 依 action type 呼叫 canonical authorities，回傳 terminal receipt/refetch，保留 RBAC/MFA/idempotency/stale/live gate/partial failure。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Journey-tracking API.
- **Original Dependencies**: `LOOP-PROD-DEP-001`, `LOOP-PROD-CAP-001`, `LOOP-PROD-TEL-002`, `LOOP-PROD-EVO-001`, `LOOP-PROD-AUTH-001`, `TJ-E2E-014`, `LOOP-PROD-PER-001`
- **Amended Dependencies**: `LOOP-PROD-DEP-001`, `LOOP-PROD-CAP-001`, `LOOP-PROD-TEL-002`, `LOOP-PROD-EVO-001`, `TJ-E2E-014`, `LOOP-PROD-PER-001`

#### LOOP-PROD-TJ-002: Hosted Trade Journey action controls

- **Summary**: 在 execute-plans 加入 action availability、confirmation、terminal receipt/refetch、authenticated SSE reconnect，以及 partial-source/degraded/error UX；完成 desktop/mobile/a11y/strict auth evidence。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Expose journey controls on FE.
- **Original Dependencies**: `LOOP-PROD-TJ-001`, `LOOP-PROD-FE-001`, `MGMT-SSE-001`, `OPS-EP-DEV-MAIN-RECONCILE-001`, `LOOP-PROD-AGORA-003`
- **Amended Dependencies**: `LOOP-PROD-TJ-001`, `MGMT-SSE-001`, `OPS-EP-DEV-MAIN-RECONCILE-001`, `LOOP-PROD-AGORA-003`

#### LOOP-PROD-VERIFY-EXEC-001: Target-dev Execution spine product verifier

- **Summary**: 在 clean target-dev 驗證 Scenario B：create→plan→binding→worker→signal/decision/order/fill/position→telemetry/Journey→incident/postmortem/evolution→real target command，含 stack restart/isolation/rollback/no-live-capital。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Verify execution routing.
- **Original Dependencies**: `LOOP-PROD-PER-001`, `LOOP-PROD-DEP-001`, `LOOP-PROD-CAP-001`, `LOOP-PROD-TEL-001`, `LOOP-PROD-TEL-002`, `LOOP-PROD-EVO-001`, `LOOP-PROD-BFF-001`, `PPL-ALLOC-012`
- **Amended Dependencies**: `LOOP-PROD-PER-001`, `LOOP-PROD-DEP-001`, `LOOP-PROD-CAP-001`, `LOOP-PROD-TEL-001`, `LOOP-PROD-TEL-002`, `LOOP-PROD-EVO-001`, `LOOP-PROD-BFF-001`, `PPL-ALLOC-012`

#### LOOP-PROD-VERIFY-HUMAN-001: Target-dev Human interaction and learning verifier

- **Summary**: 驗證 Scenario C：interaction→durable evidence→dataset/handoff→consultation或shadow evaluation→human decision→journal/OODA Learn；涵蓋 tenant/provider/unauthorized/rejected proposal 與 no direct deploy/trade。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Verify human action/learning.
- **Original Dependencies**: `LOOP-PROD-TEACH-001`, `LOOP-PROD-AGORA-001`, `LOOP-PROD-AGORA-003`, `LOOP-PROD-CONS-001`, `LOOP-PROD-IMIT-001`, `PINT-010-R2`
- **Amended Dependencies**: `LOOP-PROD-TEACH-001`, `LOOP-PROD-AGORA-001`, `LOOP-PROD-AGORA-003`, `LOOP-PROD-CONS-001`, `LOOP-PROD-IMIT-001`, `PINT-010-R2`

#### LOOP-PROD-VERIFY-KNOW-001: Target-dev Knowledge spine product verifier

- **Summary**: 在 clean target-dev 驗證 Scenario A：real source→schedule→SourceRecord→distillation draft→reviewed replication→ExperimentRun→teaching/consultation/evidence handoff，含 duplicate/source/provider failure/restart/immutable protection。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Verify knowledge routing.
- **Original Dependencies**: `LOOP-PROD-SRC-001`, `LOOP-PROD-DIST-001`, `LOOP-PROD-ALPHA-001`, `LOOP-PROD-TEACH-001`, `LOOP-PROD-AGORA-001`, `LOOP-PROD-AGORA-002`, `LOOP-PROD-AGORA-003`, `LOOP-PROD-CONS-001`, `LOOP-PROD-IMIT-001`, `LOOP-PROD-BFF-001`
- **Amended Dependencies**: `LOOP-PROD-SRC-001`, `LOOP-PROD-DIST-001`, `LOOP-PROD-ALPHA-001`, `LOOP-PROD-TEACH-001`, `LOOP-PROD-AGORA-001`, `LOOP-PROD-AGORA-002`, `LOOP-PROD-AGORA-003`, `LOOP-PROD-CONS-001`, `LOOP-PROD-IMIT-001`, `LOOP-PROD-BFF-001`

#### LOOP-PROD-VERIFY-OODA-001: Multi-persona OODA overlay product verifier

- **Summary**: 至少以三個動態 persona 各自完成 real OODA packet chain，驗證 duplicate cron、orphan repair、restart、provider outage、Learn attribution，並確認 Act 僅 proposal、無直接執行。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Verify OODA schedule.
- **Original Dependencies**: `LOOP-PROD-OODA-001`, `LOOP-PROD-VERIFY-KNOW-001`, `LOOP-PROD-VERIFY-EXEC-001`, `LOOP-PROD-MAI-001`
- **Amended Dependencies**: `LOOP-PROD-OODA-001`, `LOOP-PROD-VERIFY-KNOW-001`, `LOOP-PROD-VERIFY-EXEC-001`, `LOOP-PROD-MAI-001`

---

### Wave 3 — Convergence Tasks (Permissive Auth)

#### LOOP-PROD-MAI-003: Management AI/OpenClaw product closeout

- **Summary**: 彙整 exact BFF/FE SHAs、repair sentinel、SA/SD→task→supervisor receipts、debug/repair security negatives、restart/rollback 與 residual risk，形成 Management AI product closeout。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Reconcile management console scopes.
- **Original Dependencies**: `LOOP-PROD-MAI-001`, `LOOP-PROD-MAI-002`, `LOOP-PROD-VERIFY-OODA-001`
- **Amended Dependencies**: `LOOP-PROD-MAI-001`, `LOOP-PROD-MAI-002`, `LOOP-PROD-VERIFY-OODA-001`

#### LOOP-PROD-PINT-001: Persona Interaction reconciled hosted product closeout

- **Summary**: 只透過 OPS-EP-DEV-MAIN-RECONCILE-001→PINT-010-R2 收斂；以 exact reconciled/deployed commit、green integration gate，驗證 consultation/disagreement/revision/paper validation/journal/audit/degraded+rollback desktop/mobile。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Closeout PINT requirements.
- **Original Dependencies**: `OPS-EP-DEV-MAIN-RECONCILE-001`, `PINT-010-R2`, `LOOP-PROD-AGORA-003`, `LOOP-PROD-VERIFY-HUMAN-001`, `LOOP-PROD-FE-001`
- **Amended Dependencies**: `OPS-EP-DEV-MAIN-RECONCILE-001`, `PINT-010-R2`, `LOOP-PROD-AGORA-003`, `LOOP-PROD-VERIFY-HUMAN-001`

#### LOOP-PROD-PPL-001: Persona promotion and allocation product closeout

- **Summary**: 消費既有 PPL-ALLOC-009..013，不重建；彙整 real attribution、ranking snapshot join、terminal allocation與 authoritative weights/restart、first evaluation schedule、legitimate two-man containment post-state、hosted create/paper/review/allocation UX。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Closeout allocation/promotion goals.
- **Original Dependencies**: `PPL-ALLOC-009`, `PPL-ALLOC-010`, `PPL-ALLOC-011`, `PPL-ALLOC-012`, `PPL-ALLOC-013`, `LOOP-PROD-PER-001`, `LOOP-PROD-VERIFY-EXEC-001`, `LOOP-PROD-FE-001`
- **Amended Dependencies**: `PPL-ALLOC-009`, `PPL-ALLOC-010`, `PPL-ALLOC-011`, `PPL-ALLOC-012`, `PPL-ALLOC-013`, `LOOP-PROD-PER-001`, `LOOP-PROD-VERIFY-EXEC-001`

#### LOOP-PROD-TJ-003: Trade Journey superseding product closeout

- **Summary**: 保留已完成 TJ-E2E-012 為舊 evidence，不重開；以 TJ-E2E-014、新 canonical projector/actions/UI 與 execution verifier 產生新的 product closeout。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: Reconcile trade journey e2e.
- **Original Dependencies**: `TJ-E2E-012`, `TJ-E2E-014`, `LOOP-PROD-TJ-001`, `LOOP-PROD-TJ-002`, `LOOP-PROD-VERIFY-EXEC-001`
- **Amended Dependencies**: `TJ-E2E-012`, `TJ-E2E-014`, `LOOP-PROD-TJ-001`, `LOOP-PROD-TJ-002`, `LOOP-PROD-VERIFY-EXEC-001`

---

### Wave 4 — G2 Foundational Checkpoint (Permissive Auth)

#### LOOP-PROD-CLOSE-001: Global 12-loop plus OODA product closeout

- **Summary**: 從 clean target-dev 重跑四大 scenarios；12 canonical loops 加 OODA overlay 全部要有 current controller records、無 registry-only truth，maturity 僅由 evidence 推導，並取得獨立 Human/Ops verdict。
- **Classification**: permitted before the paper-trade proof
- **Rationale**: End of the permissive paper-trade loop.
- **Original Dependencies**: `LOOP-PROD-002`, `LOOP-PROD-AUTH-001`, `LOOP-PROD-FE-001`, `LOOP-PROD-REC-001`, `LOOP-PROD-VERIFY-KNOW-001`, `LOOP-PROD-VERIFY-EXEC-001`, `LOOP-PROD-VERIFY-HUMAN-001`, `LOOP-PROD-VERIFY-OODA-001`, `LOOP-PROD-PPL-001`, `LOOP-PROD-TJ-003`, `LOOP-PROD-PINT-001`, `LOOP-PROD-MAI-003`
- **Amended Dependencies**: `LOOP-PROD-002`, `LOOP-PROD-REC-001`, `LOOP-PROD-VERIFY-KNOW-001`, `LOOP-PROD-VERIFY-EXEC-001`, `LOOP-PROD-VERIFY-HUMAN-001`, `LOOP-PROD-VERIFY-OODA-001`, `LOOP-PROD-PPL-001`, `LOOP-PROD-TJ-003`, `LOOP-PROD-PINT-001`, `LOOP-PROD-MAI-003`

---

### Wave 5 — Hardening Wave: Security & Release Cutover (Strict Auth)

#### LOOP-PROD-ATTEST-001: Protected product attestation trust root

- **Summary**: Protected controller 從 immutable raw artifacts 產生 canonical attestation，並以 candidate 無法取得的 asymmetric key 或 platform-protected keyed identity 簽署；unkeyed checksum 只可作為 signed envelope 內的內容摘要。
- **Classification**: deferred strict-auth/security/governance work
- **Rationale**: Cryptographic task evidence signer.
- **Original Dependencies**: `LOOP-PROD-002`, `LOOP-PROD-WORKER-001`, `LOOP-PROD-LEASE-001`
- **Amended Dependencies**: `LOOP-PROD-002`, `LOOP-PROD-WORKER-001`, `LOOP-PROD-LEASE-001`

#### LOOP-PROD-AUTH-001: Strict dev auth cutover and exact BFF build identity

- **Summary**: 沿用既有 /bff/auth/dev-login，將 hosted dev 切到 AUTH_STUB=false/strict；使用短效 role/tenant identities，移除 default all-role bearer，並在 /bff/version 暴露非敏感 git SHA、image digest、build time、environment、config posture。
- **Classification**: deferred strict-auth/security/governance work
- **Rationale**: Strict dev auth cutover.
- **Original Dependencies**: `LOOP-PROD-002`
- **Amended Dependencies**: `LOOP-PROD-002`, `LOOP-PROD-CLOSE-001`

#### LOOP-PROD-AUTH-BOOT-001: Authorized dev auth credential bootstrap

- **Summary**: 在 strict auth hosted 驗收之前，由獲授權 Human/Ops 於受保護環境建立 dev signing、dev-login client 與最小權限身份；fleet 僅能驗證 redacted metadata，不能產生、讀取或輸出 secret。
- **Classification**: deferred strict-auth/security/governance work
- **Rationale**: Load strict credentials to vault.
- **Original Dependencies**: `LOOP-PROD-002`, `LOOP-PROD-DELIVERY-001`
- **Amended Dependencies**: `LOOP-PROD-002`, `LOOP-PROD-DELIVERY-001`

#### LOOP-PROD-AUTH-OPS-001: Governed dev credential and privileged-capability lifecycle

- **Summary**: 建立 dev JWT signing、dev-login client、role/tenant identity 與 assistant.kernel capability 的治理、rotation、expiry 與 hosted proof；未授權時保持 BLOCKED。
- **Classification**: deferred strict-auth/security/governance work
- **Rationale**: MFA/Ops credentials rotation.
- **Original Dependencies**: `LOOP-PROD-AUTH-BOOT-001`, `LOOP-PROD-AUTH-001`, `LOOP-PROD-LEASE-001`, `LOOP-PROD-ATTEST-001`
- **Amended Dependencies**: `LOOP-PROD-AUTH-BOOT-001`, `LOOP-PROD-AUTH-001`, `LOOP-PROD-LEASE-001`, `LOOP-PROD-ATTEST-001`

#### LOOP-PROD-BROWSER-AUTH-001: Coordinated credential-free browser auth cutover

- **Summary**: 把 BFF strict auth、execute-plans credential-free build、完整 viewer route matrix 與 hosted browser 驗收綁成同一 cutover lease；任一側未就緒就不得啟用，且可原子回滾兩側。
- **Classification**: deferred strict-auth/security/governance work
- **Rationale**: BFF cookie/session strict protection.
- **Original Dependencies**: `LOOP-PROD-AUTH-BOOT-001`, `LOOP-PROD-AUTH-001`, `LOOP-PROD-FE-001`, `LOOP-PROD-DELIVERY-001`, `LOOP-PROD-LEASE-001`, `LOOP-PROD-AUTH-OPS-001`
- **Amended Dependencies**: `LOOP-PROD-AUTH-BOOT-001`, `LOOP-PROD-AUTH-001`, `LOOP-PROD-FE-001`, `LOOP-PROD-DELIVERY-001`, `LOOP-PROD-LEASE-001`, `LOOP-PROD-AUTH-OPS-001`

#### LOOP-PROD-DELIVERY-001: Fleet-only delivery provenance and independent review admission

- **Summary**: 把 planner、fleet worker、fleet reviewer 的權限邊界做成 fail-closed delivery gate；沒有 canonical task、exact run/worktree/scope binding、獨立正式 review 或唯一 lease 的 PR 一律不能合併。
- **Classification**: deferred strict-auth/security/governance work
- **Rationale**: Ensure worker run/scope signing.
- **Original Dependencies**: `LOOP-PROD-002`
- **Amended Dependencies**: `LOOP-PROD-002`, `LOOP-PROD-CLOSE-001`

#### LOOP-PROD-FE-001: Gate-before-deploy safe execute-plans release

- **Summary**: 重構 execute-plans dev release：exact candidate SHA integration gate 先通過，candidate pre-probe 後才 atomic switch，post-switch failure 自動 rollback；safe writes default false，browser 不含 bearer/client secret，並拒絕 out-of-order deploy。
- **Classification**: deferred strict-auth/security/governance work
- **Rationale**: Remove browser development tokens.
- **Original Dependencies**: `LOOP-PROD-002`, `LOOP-PROD-AUTH-001`
- **Amended Dependencies**: `LOOP-PROD-002`, `LOOP-PROD-AUTH-001`

#### LOOP-PROD-FLEET-001: Fair, quota-aware, starvation-bounded fleet admission

- **Summary**: 建立 age-aware、公平、quota reset aware 的 owner/reviewer admission；hot retry 必須 quarantine，不能長期佔用 reservation 讓較舊 ready work 飢餓。
- **Classification**: deferred strict-auth/security/governance work
- **Rationale**: Quota & resource admission boundaries.
- **Original Dependencies**: `LOOP-PROD-WORKER-001`
- **Amended Dependencies**: `LOOP-PROD-WORKER-001`

#### LOOP-PROD-LEASE-001: Protected shared-dev mutation lease and payload isolation

- **Summary**: Dev deploy、OpenClaw、public smoke、Agora 都必須綁定同一 controller-issued lease；candidate 不得繼承 runner cloud credentials，release 前必須有 local/remote zero-member proof。
- **Classification**: deferred strict-auth/security/governance work
- **Rationale**: Mutation lease.
- **Original Dependencies**: `LOOP-PROD-AUTH-BOOT-001`, `LOOP-PROD-AUTH-001`, `LOOP-PROD-WORKER-001`
- **Amended Dependencies**: `LOOP-PROD-AUTH-BOOT-001`, `LOOP-PROD-AUTH-001`, `LOOP-PROD-WORKER-001`

#### LOOP-PROD-WORKER-001: Exact-CAS worker outcome and forced termination integrity

- **Summary**: 所有 launch、resume、retry 與 terminal outcome 都必須以 exact task/event/owner/run/payload signature 做 admission/CAS；失去 ownership 的 process group 與 file-inbox payload 必須確認歸零。
- **Classification**: deferred strict-auth/security/governance work
- **Rationale**: Fleet container outcome and CAS.
- **Original Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-DELIVERY-001`
- **Amended Dependencies**: `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-DELIVERY-001`

---

### Wave 6 — Hardening Wave: Verification & Sign-off

#### LOOP-PROD-FE-BUILD-001: Warning-free, budgeted live/strict product build

- **Summary**: 最後 feature-bearing live/strict/safe-write build 必須無 invalid CSS、circular chunk、unexpected chunk-load error，並通過明確 bundle budget 與 hosted desktop/mobile quality gate。
- **Classification**: final verification/closeout after the appropriate gate
- **Rationale**: Strict build budget verification.
- **Original Dependencies**: `LOOP-PROD-FE-001`, `LOOP-PROD-FE-EVID-001`, `LOOP-PROD-AGORA-003`, `LOOP-PROD-TJ-002`, `LOOP-PROD-MAI-002`
- **Amended Dependencies**: `LOOP-PROD-FE-001`, `LOOP-PROD-FE-EVID-001`, `LOOP-PROD-AGORA-003`, `LOOP-PROD-TJ-002`, `LOOP-PROD-MAI-002`

#### LOOP-PROD-FE-EVID-001: Fail-closed protected-attestation consumer

- **Summary**: execute-plans release gate 只接受 protected controller attestation；candidate booleans、zero-count、fixture、snapshot 或 self-signed manifest 一律不能解鎖部署。
- **Classification**: final verification/closeout after the appropriate gate
- **Rationale**: Frontend checks attestation digests.
- **Original Dependencies**: `LOOP-PROD-FE-001`, `LOOP-PROD-ATTEST-001`, `LOOP-PROD-AGORA-003`, `LOOP-PROD-TJ-002`, `LOOP-PROD-MAI-002`
- **Amended Dependencies**: `LOOP-PROD-FE-001`, `LOOP-PROD-ATTEST-001`, `LOOP-PROD-AGORA-003`, `LOOP-PROD-TJ-002`, `LOOP-PROD-MAI-002`

#### LOOP-PROD-SIGNOFF-001: Protected Human/Ops completion verdict enforcement

- **Summary**: 在 final closeout 前安裝機器守門：所有 requires_human_ops_signoff 任務必須有受保護、可撤銷、不可重播且綁定 exact catalog、manifest、target 與部署 identity 的 Human/Ops 判決；fleet 不得自行簽發。
- **Classification**: final verification/closeout after the appropriate gate
- **Rationale**: Enforces manual signoff gating.
- **Original Dependencies**: `LOOP-PROD-CLOSE-001`, `LOOP-PROD-WORKER-001`, `LOOP-PROD-ATTEST-001`
- **Amended Dependencies**: `LOOP-PROD-CLOSE-001`, `LOOP-PROD-WORKER-001`, `LOOP-PROD-ATTEST-001`

---

### Wave 7 — Hardening Wave: Program Final Verdict

#### LOOP-PROD-CLOSE-002: Final primary-catalog closeout after runtime bootstrap

- **Summary**: 從 clean target-dev 重跑 baseline 四大 scenarios 與 additive safety matrix；其餘 47 個 primary tasks、所有 external dependencies（包含已收斂之 EVOCHAIN-011、EVOLOOP-009 與 EVOLOOP-011）、fleet-only delivery provenance、coordinated browser auth、protected evidence、strict auth bootstrap/ops、fleet fairness、worker/lease integrity、受保護簽核與 warning-free frontend 全部通過後，guarded finalization 才可完成第 48 個任務並宣告 program 完成。
- **Classification**: final verification/closeout after the appropriate gate
- **Rationale**: Final program closeout verification.
- **Original Dependencies**: `EVOCHAIN-011`, `EVOLOOP-009`, `EVOLOOP-011`, `LOOP-PROD-CLOSE-001`, `LOOP-PROD-DELIVERY-001`, `LOOP-PROD-WORKER-001`, `LOOP-PROD-LEASE-001`, `LOOP-PROD-BROWSER-AUTH-001`, `LOOP-PROD-FLEET-001`, `LOOP-PROD-ATTEST-001`, `LOOP-PROD-AUTH-OPS-001`, `LOOP-PROD-FE-EVID-001`, `LOOP-PROD-FE-BUILD-001`, `LOOP-PROD-SIGNOFF-001`
- **Amended Dependencies**: `EVOCHAIN-011`, `EVOLOOP-009`, `EVOLOOP-011`, `LOOP-PROD-CLOSE-001`, `LOOP-PROD-DELIVERY-001`, `LOOP-PROD-WORKER-001`, `LOOP-PROD-LEASE-001`, `LOOP-PROD-BROWSER-AUTH-001`, `LOOP-PROD-FLEET-001`, `LOOP-PROD-ATTEST-001`, `LOOP-PROD-AUTH-OPS-001`, `LOOP-PROD-FE-EVID-001`, `LOOP-PROD-FE-BUILD-001`, `LOOP-PROD-SIGNOFF-001`

---
