---
project: Pantheon
document_type: System Analysis Gap Report
revision: v2-corrected
language: zh-TW
updated_reason: >
  依據最新 Codex 盤點校正：目前 Pantheon 實際接入的是 `pantheon/lean` submodule，
  remote 為 `ajoe734/pantheon-lean.git`，且已含 PantheonAlgoBase / Pantheon LEAN bridge。
  `lean-platform` 雖已 clone，但不是目前 Pantheon 實際接的 Lean repo，且未命中 Pantheon / RuntimeBinding / SignalStore 等整合訊號。
baseline_note: >
  本批修正版不再將 `Lean` 與 `lean-platform` 粗略二分，而是明確區分：
  1) `pantheon/lean` submodule / `ajoe734/pantheon-lean.git` = 目前實際 bridge；
  2) `ajoe734/lean-platform` = 未對齊 / 歷史或遷移候選；
  3) generic upstream Lean = LEAN engine 基底概念。
---

# SA-20 v2 — Risk Register：以 `pantheon/lean` 實際 Bridge 校正

## 1. 本章修正重點

本章取代原 SA-20。
新版 Risk Register 將原本的「Lean vs lean-platform」風險修正為更精準的：

```text
Blueprint repo mapping = lean-platform
Actual current bridge = pantheon/lean submodule / ajoe734/pantheon-lean.git
```

同時加入 Codex 盤點中的新風險狀態：

```text
active task board empty
paper runtime baseline exists
live health-only sidecar
bracket order guarded paper/sim execution
source/search bounded baseline
research/learning fail-closed
OpenClaw facade not broker kernel
frontend demo auth / demo islands
health endpoint cleanup
env-gated Postgres adoption
```

---

## 2. Top 15 risks revised

| Rank | Risk | Severity | Revised Interpretation |
|---|---|---|---|
| 1 | Blueprint says lean-platform but actual bridge is pantheon/lean | Critical | repo mapping drift |
| 2 | Production live runtime not implemented | Critical | paper baseline exists, live is health-only |
| 3 | RuntimeBinding context propagation unverified | Critical | bridge exists but contract needs proof |
| 4 | TelemetryEvent exporter from paper runtime unverified | High | schema can be tested first in paper; kill-switch ack now fail-closes when runtime follow-through is missing |
| 5 | Full Lean Launcher + broker SDK kernel not active | Critical | production execution gap |
| 6 | Bracket order is guarded outside paper/sim | High | paper/sim bracket execution now has explicit guard; live remains fail-closed |
| 7 | Frontend auth still demo/local-token | High | production adoption gap |
| 8 | Frontend demo islands remain | High | operator console adoption gap |
| 9 | lean-platform stale but still in blueprint | High | Codex patch wrong repo |
| 10 | BFF HA/LB deferred | Medium | intentional, not bug |
| 11 | Data persistence env-gated, dev JSON/JSONL fallback | Medium | rollout gap |
| 12 | Source/search bounded, not unrestricted crawler | Low-Medium | correct bounded stance |
| 13 | Research/learning production adapters fail-closed | Medium | intentional safety |
| 14 | OpenClaw broker/live disabled | Medium | correct boundary |
| 15 | control/exec compose legacy health endpoints | Medium | cleanup gap |

---

## 3. Architecture risks

### R-ARCH-001 — Repo mapping drift: blueprint `lean-platform`, actual `pantheon/lean`

```text
Category: Architecture / Repo Ownership
Description:
  原藍圖指定 lean-platform，但目前 pantheon 實際透過 submodule lean/ 接 ajoe734/pantheon-lean.git。
Impact:
  Codex patch 錯 repo、文件錯誤、CI 測錯、migration 混亂。
Likelihood: Certain
Impact: Critical
Severity: Critical
Mitigation:
  - ADR-EXEC-001 v2
  - 更新 blueprint repo mapping
  - CI assert .gitmodules remote
  - task packet official target
Acceptance:
  所有 execution task 明確 target pantheon/lean，除非 ADR 指示 migrate。
```

### R-ARCH-002 — pantheon-lean bridge 未正式文件化

```text
Category: Architecture
Description:
  PantheonAlgoBase bridge 存在，但 bridge ownership / API / context contract 若未文件化，後續容易漂移。
Likelihood: High
Impact: High
Severity: High
Mitigation:
  - PANTHEON_LEAN_BRIDGE.md
  - runtime context schema
  - bridge smoke tests
Acceptance:
  bridge file documents runtime context, telemetry, activation scope.
```

### R-ARCH-003 — lean-platform orphan risk

```text
Category: Repo Governance
Description:
  lean-platform 已 clone 但未集成，仍可能被錯誤使用。
Likelihood: High
Impact: High
Severity: High
Mitigation:
  - archive / label as migration candidate
  - no P0 task target
  - migration-only ADR
Acceptance:
  lean-platform README/status clearly says not current Pantheon runtime.
```

---

## 4. Execution risks

### R-EXE-001 — Live role is health-only but mistaken as live-ready

```text
Category: Execution
Description:
  runtime_bootstrap 對 live placeholder 是 health-only sidecar；若被誤認為 production live runtime，會造成重大風險。
Likelihood: Medium
Impact: Critical
Severity: Critical
Mitigation:
  - fail-closed live mode
  - UI status: live_placeholder
  - activation flag required
Acceptance:
  live role reports health_only / not_activated and cannot connect broker or place orders unless explicit production activation guard passes.
```

### R-EXE-002 — Paper baseline over-interpreted as production kernel

```text
Category: Execution Maturity
Description:
  truthful paper runtime baseline 已有，但不是 full Lean Launcher + broker SDK production kernel。
Likelihood: High
Impact: High
Severity: High
Mitigation:
  - maturity labels
  - paper-only DoD
  - separate canary/live roadmap
Acceptance:
  docs label current execution maturity as paper baseline.
```

### R-EXE-003 — Bracket order guarded outside paper/sim

```text
Category: Execution / Risk Controls
Description:
  風控 bracket order 已在 paper/sim runtime 內以顯式 guard 建立 simulated child legs；
  缺少 paper/sim guard、非 entry signal、無法建立 child legs，或 live/canary scope 仍必須 log-only / fail-closed。
Likelihood: High
Impact: High
Severity: High
Mitigation:
  - paper/sim-only bracket execution guard
  - explicit logged_only vs submitted_to_broker marker
  - broker simulation tests
  - live/canary fail-closed tests
Acceptance:
  paper/sim bracket_order_logged distinguishes logged_only from submitted_to_broker;
  guarded paper/sim submissions record submitted_to_broker=true and open simulated child legs;
  live broker submission remains fail-closed without activation guard.
```

### R-EXE-004 — RuntimeBinding propagation unclear

```text
Category: Runtime Identity
Description:
  bridge 存在，但 runtime_binding_id 是否進 paper runtime / telemetry 尚需驗證。
Likelihood: Medium
Impact: Critical
Severity: Critical
Mitigation:
  - RuntimeBinding context test
  - launch contract
Acceptance:
  runtime heartbeat includes runtime_binding_id or explicitly records why unavailable in dev.
```

### R-EXE-005 — Kill switch treated as UI/audit-only action

```text
Category: Execution / Emergency Control
Description:
  Kill switch 若只更新 UI/status 或只留下 audit，而未確認 RuntimeBinding follow-through，
  operator 可能誤以為 runtime 已 pause / risk_off / liquidate。
Current status:
  P1-KILL-001 已在 runtime-manager 補 secondary path 與 telemetry_ack：
  有 RuntimeBinding follow-through 才 acknowledged；缺少 runtime ack 則 fail_closed。
Likelihood: Low-Medium
Impact: Critical
Severity: High
Mitigation:
  - RuntimeManager KillSwitchBridge
  - KillSwitchAuditEntry / AuditAction
  - telemetry_ack fail-closed semantics
Acceptance:
  kill switch result includes AuditAction evidence, RuntimeBinding state mutation,
  and telemetry_ack acknowledged/fail_closed status.
```

---

## 5. Telemetry / reconciliation risks

### R-TEL-001 — Telemetry schema exists but paper producer unverified

```text
Category: Telemetry
Description:
  TelemetryEvent schema 設計成熟，但 paper runtime 是否產出符合 schema 的 event 仍需驗證。
Likelihood: Medium
Impact: High
Severity: High
Mitigation:
  - paper heartbeat exporter
  - schema validation test
Acceptance:
  paper runtime heartbeat accepted by telemetry ingest.
```

### R-TEL-002 — Reconciliation not yet tied to paper runtime

```text
Category: Reconciliation
Description:
  paper runtime baseline 若未產生足夠 telemetry，無法做 baseline reconciliation。
Likelihood: Medium
Impact: High
Severity: High
Mitigation:
  - basic paper-vs-expected comparator
  - ReconciliationRecord smoke
Acceptance:
  one paper run produces ReconciliationRecord.
```

---

## 6. Data / source / search risks

### R-DATA-001 — Misclassifying bounded source/search as missing

```text
Category: Analysis / Planning
Description:
  source/search 已有 bounded connector/indexer baseline，若仍判定為完全缺失，會錯排優先級。
Likelihood: High
Impact: Medium
Severity: Medium
Mitigation:
  - update SA gap type to bounded baseline
  - focus on expansion / production hardening
Acceptance:
  SA documents source/search as bounded autonomous baseline.
```

### R-DATA-002 — Bounded connector mistaken as unrestricted crawler

```text
Category: Data Governance
Description:
  現有 source/search 是 bounded connector/indexer，不是 unrestricted autonomous crawler。
Likelihood: Medium
Impact: Medium
Severity: Medium
Mitigation:
  - explicit non-goal
  - guarded external_feed policy
Acceptance:
  no crawler behavior unless explicit connector config and guard.
```

### R-DATA-003 — Dev JSON/JSONL fallback mistaken as production durable storage

```text
Category: Persistence
Description:
  default dev 仍可 JSON/JSONL，staging/prod posture 有 durable guard。
Likelihood: Medium
Impact: Medium
Severity: Medium
Mitigation:
  - services.foundation.persistence_posture shared guard
  - environment posture labels in health/runtime metadata
  - staging/prod guard tests and platform posture script
Acceptance:
  staging/prod fail without Postgres/object store when required; dev JSON/JSONL fallback is explicitly dev-only.
```

### R-DATA-004 — News/social/alpha DB connectors bypass source/evidence governance

```text
Category: Data Governance
Description:
  news / social / external alpha DB 若只作為 vendor feed、research script、Lean feed 或 broker-side signal，
  會繞過 SourceRecord / EvidenceBundle、ACL、license、entitlement、available_time 與 PIT 語意。
Likelihood: Medium
Impact: High
Severity: High
Mitigation:
  - P1-SOURCE-001 external source policy validator
  - connector-level entitlement_tags / entitlement_ref requirement
  - source-family required metadata for news, social, alpha_db
  - EvidenceBundle available_time and entitlement_tags preservation
  - direct Lean / broker / runtime / execution route rejection
Acceptance:
  news/social/alpha_db connector ingest emits governed SourceRecord and EvidenceBundle refs;
  missing entitlement, missing available_time/PIT fields, unsafe social trust metadata,
  unsafe alpha allowed_use, or direct Lean/broker target is rejected before search or runtime use.
Status:
  Mitigated for bounded source-ingest baseline by P1-SOURCE-001; production vendor credential
  activation and provider procurement remain separate rollout work.
```

---

## 7. Research / OpenClaw risks

### R-OSS-001 — Fail-closed adapters mistaken as missing work

```text
Category: Research Activation
Description:
  Qlib / TRL / FinRL / RLlib / Ray / W&B production adapters fail-closed 是 safety posture，不是單純缺失。
Likelihood: Medium
Impact: Medium
Severity: Medium
Mitigation:
  - mark as pre-activation
  - offline smoke allowed
Acceptance:
  docs distinguish scaffold / smoke / production activation.
```

### R-OC-001 — OpenClaw facade mistaken as execution kernel

```text
Category: Governance
Description:
  OpenClaw adapter 是 Pantheon-owned boundary facade，不是 live broker kernel。
Likelihood: Medium
Impact: High
Severity: High
Mitigation:
  - keep broker/live/capital binding env gates off
  - no direct broker tool
Acceptance:
  OpenClaw cannot trade or access broker credentials by default.
```

---

## 8. Frontend / auth risks

### R-FE-001 — Demo auth remains in frontend

```text
Category: Production Adoption
Description:
  BFF auth baseline 比前端登入流成熟；前端 AuthProvider 仍有 demo import / demo token。
Likelihood: High
Impact: High
Severity: High
Mitigation:
  - replace demo AuthProvider in staging/prod
  - OIDC / enterprise login path
Acceptance:
  staging/prod front has no @/demo auth imports and no demo copy.
```

### R-FE-002 — Demo islands remain in route-live UI

```text
Category: UI / Operator Readiness
Description:
  多數主線 route 已接 BFF，但 dashboard/persona tabs/health/evolution/tools/settings/trainer 仍有 demo islands。
Likelihood: High
Impact: High
Severity: High
Mitigation:
  - demo island inventory
  - source_mode badge
  - production route no-demo CI
Acceptance:
  production operator routes cannot import @/demo.
```

---

## 9. Operational risks

### R-OPS-001 — Health endpoint inconsistency

```text
Category: Ops Cleanup
Description:
  default compose 大致使用 /readyz，但 control/exec compose 仍有 __health__。
Likelihood: High
Impact: Medium
Severity: Medium
Mitigation:
  - health endpoint cleanup
  - CI scan compose files
Acceptance:
  control/exec compose uses /healthz /livez /readyz /metrics consistently.
```

### R-OPS-002 — Active task board empty hides untracked artifacts

```text
Category: Coordination
Description:
  active task board 空，但有未追蹤 .orchestrator/chair-reviews/* 產物，不是 code modification。
Likelihood: Medium
Impact: Low-Medium
Severity: Low
Mitigation:
  - separate artifacts from implementation changes
  - clean or commit docs intentionally
Acceptance:
  untracked chair reviews classified as artifacts, not code progress.
```

### R-OPS-003 — Staging split mistaken as HA production

```text
Category: Deployment
Description:
  control/exec 2VM compose split 已有，但 BFF HA/LB deferred。
Likelihood: Medium
Impact: Medium
Severity: Medium
Mitigation:
  - mark 2VM split as staging contract
  - mark HA/LB deferred
Acceptance:
  docs distinguish staging split from HA production topology.
```

---

## 10. Revised top P0 mitigations

```text
1. ADR-EXEC-001 v2: official pantheon/lean bridge policy
2. Update blueprint repo mapping away from lean-platform or define migration
3. CI: assert pantheon/lean submodule remote
4. Runtime bootstrap contract: DeploymentPlan → runtime_bootstrap
5. Paper runtime telemetry heartbeat
6. Live placeholder fail-closed guard
7. Bracket order status labeling
8. Frontend demo auth removal for staging/prod
9. Frontend demo island inventory
10. Health endpoint cleanup in control/exec compose
```

---

## 11. Revised risk conclusion

SA-20 v2 的風險結論：

> **目前風險狀態比原本更正面：foundation、source/search bounded baseline、OpenClaw facade、pantheon-lean bridge、paper runtime baseline 都已存在。但最大 P0 風險仍在 execution repo mapping 與 production runtime maturity：藍圖寫 lean-platform，實作接 pantheon/lean；paper baseline 有，live 仍是 health-only；bracket order log-only；前端 auth/demo islands 仍阻止 production operator readiness。**

這些風險如果按優先順序處理，Pantheon 可以從「foundation materialized」推進到「paper operating loop verified」。
