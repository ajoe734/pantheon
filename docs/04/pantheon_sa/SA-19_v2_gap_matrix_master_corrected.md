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

> **2026-05-03 Canonical correction**: `pantheon/lean` submodule backed by `ajoe734/pantheon-lean.git` is the official execution substrate. Any older `lean-platform` repo-mapping drift language in this SA note is superseded; do not treat `lean-platform` as an active gap or task target.





# SA-19 v2 — Gap Matrix 總表：以 `pantheon/lean` 實際 Bridge 校正

## 1. 本章修正重點

本章取代原 SA-19。
核心修正：

```text
Execution repo gap 不再寫成 Lean vs lean-platform 的泛稱，
而是改為：

Blueprint canonicalized: pantheon/lean submodule → ajoe734/pantheon-lean.git
No current execution gap is tracked for lean-platform.
```

並且加入 Codex 盤點中的新事實：

```text
active task board 空
pantheon branch backend-dev-publish-20260429
front repo path 是 /home/lupin/code/front-ai-trading-system
execution Dockerfile Python slim
runtime_bootstrap paper baseline
live health-only sidecar
source/search bounded baseline
research/learning production posture / no direct order routing
OpenClaw facade
frontend demo islands
health endpoint cleanup
```

---

## 2. 新 Gap Type

本版新增 gap 類別：

| Type | 定義 |
|---|---|
| Actual Gap | 需要補實作 |
| Intentional Deferral | 明確延後，不是 bug |
| Safety Gate | 為了安全而 fail-closed |
| Dev-only Fallback | dev baseline 保留 fallback |
| Production Adoption Gap | dev 可用，但 production 尚未 adoption |
| Repo Mapping Drift | 藍圖 repo 與實際 repo 不一致 |
| Cleanup Gap | 小型一致性清理 |
| Activation Gap | scaffold 有，但未啟用 production |

---

## 3. Top-level progress matrix

| Area | Current State | Gap Type | Severity |
|---|---|---|---|
| Active task board | `.tasks | length == 0`, workload 0 | Operational State | Low |
| pantheon branch | `backend-dev-publish-20260429` | Info | Low |
| chair reviews | untracked `.orchestrator/chair-reviews/*` | Non-code artifact | Low |
| front repo path | `/home/lupin/code/front-ai-trading-system` | Repo path correction | Medium |
| execution bridge | `pantheon/lean` submodule / `pantheon-lean.git` | Canonical current implementation | Low |
| lean-platform | historical/non-target clone | Superseded; do not track as active gap | None |
| execution runtime | paper baseline + live health-only | Production Runtime Gap | Critical |
| staging 2VM | compose split exists | Intentional partial | Medium |
| BFF HA/LB | deferred | Intentional Deferral | Medium |
| data persistence | env-gated Postgres + dev JSON/JSONL fallback | Dev-only fallback | Medium |
| source/search | bounded autonomous baseline | Good progress | Medium |
| research/learning | production data/model posture required before promotion; no direct order routing | Activation Boundary | Medium |
| OpenClaw | facade, broker/live off | Correct boundary | Medium |
| auth | backend stronger than frontend | Production Adoption Gap | High |
| frontend | route-live many, demo islands remain | Production Adoption Gap | High |
| health endpoints | default mostly readyz, staging legacy remains | Cleanup Gap | Medium |

---

## 4. Repo ownership gap matrix v2

| ID | Responsibility | Blueprint | Current | Gap | Severity | Action |
|---|---|---|---|---|---|---|
| RO-001 | Execution substrate | old docs named lean-platform | pantheon/lean submodule | canonicalized 2026-05-03 | Low | keep pantheon-lean as target |
| RO-002 | LEAN bridge remote | not specified in old SA | ajoe734/pantheon-lean.git | needs formal authority | Critical | submodule policy |
| RO-003 | Bridge class | N/A | PantheonAlgoBase | bridge exists | Medium | verify context contract |
| RO-004 | lean-platform | historical wording | not current runtime target | superseded | None | no active work unless future migration ADR |
| RO-005 | Runtime bootstrap | not fully mapped | runtime_bootstrap.py | baseline exists | High | formal contract |
| RO-006 | Full live kernel | expected future | health-only sidecar | production gap | Critical | activation roadmap |

---

## 5. Execution gap matrix v2

| ID | Capability | Current | Gap Type | Severity |
|---|---|---|---|---|
| EXE-001 | Pantheon LEAN bridge | `pantheon/lean` + PantheonAlgoBase | implemented baseline | Medium |
| EXE-002 | Paper runtime | Python paper runtime | implemented baseline | Medium |
| EXE-003 | Live runtime | health-only sidecar | Safety Gate / Production Gap | Critical |
| EXE-004 | Full Lean Launcher production | not current kernel | Actual Gap | Critical |
| EXE-005 | Broker SDK live execution | not enabled | Actual Gap / Activation | Critical |
| EXE-006 | Bracket orders | log-only | Actual Gap | High |
| EXE-007 | DeploymentPlan → runtime_bootstrap | not formalized | Contract Gap | High |
| EXE-008 | RuntimeBinding context | needs proof | Contract Gap | High |
| EXE-009 | TelemetryEvent exporter | needs proof | Telemetry Gap | High |
| EXE-010 | Canary/live activation | not enabled | Intentional / Actual Gap | High |

---

## 6. Source/Search gap matrix v2

| ID | Capability | Current | Revised Judgment | Severity |
|---|---|---|---|---|
| SRC-001 | Configured connectors | present | baseline exists | Medium |
| SRC-002 | Scheduler | present | baseline exists | Medium |
| SRC-003 | DLQ | present | baseline exists | Medium |
| SRC-004 | Frontier | present | baseline exists | Medium |
| SRC-005 | Audit replay | present | baseline exists | Medium |
| SRC-006 | static_records | present | bounded fetch mode | Low |
| SRC-007 | guarded external_feed | present | bounded external mode | Medium |
| SRC-008 | unrestricted crawler | not present | intentional non-goal for now | Low |
| SRC-009 | social / alpha DB | not production | expansion gap | Medium |
| SRC-010 | OpenClaw governed search | needs proof | integration gap | High |

---

## 7. Research / learning / OSS gap matrix v2

| ID | Area | Current | Gap Type | Severity |
|---|---|---|---|---|
| OSS-001 | Qlib | production data/model activation requires posture and artifact-promotion evidence | Activation Boundary | Medium |
| OSS-002 | TRL | production data/model activation requires posture and artifact-promotion evidence | Activation Boundary | Medium |
| OSS-003 | FinRL | production data/model activation requires posture and artifact-promotion evidence | Activation Boundary | Medium |
| OSS-004 | RLlib / Ray | production data/model activation requires posture and artifact-promotion evidence | Activation Boundary | Medium |
| OSS-005 | W&B | online backend re-entry deferred; no direct production registry promotion | Activation Boundary | Low-Medium |
| OSS-006 | offline smoke | allowed direction | pre-activation | Medium |
| OSS-007 | production activation | not enabled | Intentional Deferral | Medium |

---

## 8. OpenClaw gap matrix v2

| ID | Capability | Current | Judgment | Severity |
|---|---|---|---|---|
| OC-001 | Pantheon-owned boundary facade | present | good progress | Medium |
| OC-002 | production broker adapter | off by env gate | correct boundary | Medium |
| OC-003 | paper adapter | off by default | activation gap | Medium |
| OC-004 | live adapter | off by default | safety gate | High |
| OC-005 | capital binding | off by default | needs activation plan | High |
| OC-006 | OpenClaw as broker kernel | not enabled | correct, not a bug | Low |
| OC-007 | governed search | needs proof | integration gap | High |

---

## 9. Frontend gap matrix v2

| ID | Area | Current | Gap Type | Severity |
|---|---|---|---|---|
| FE-001 | BFF client | centralized route-live | progress | Medium |
| FE-002 | App routes | operator/research/knowledge/consultation/governance mounted | progress | Medium |
| FE-003 | AuthProvider | imports `@/demo/api` | Production Adoption Gap | High |
| FE-004 | demo token | writes `pantheon_operator_token` | Production Adoption Gap | High |
| FE-005 | Login | demo copy remains | Cleanup / Adoption | Medium |
| FE-006 | dashboard/persona/health/evolution/tools/settings/trainer | demo islands remain | Production Adoption Gap | High |
| FE-007 | enterprise/OIDC login | not complete | Actual Gap | High |
| FE-008 | source_mode marking | needs proof | Verification Gap | Medium |

---

## 10. BFF / auth gap matrix v2

| ID | Area | Current | Gap Type | Severity |
|---|---|---|---|---|
| BFF-001 | HS256 JWT | present | progress | Medium |
| BFF-002 | optional JWKS/OIDC | present | progress | Medium |
| BFF-003 | frontend auth | demo/local-token | adoption gap | High |
| BFF-004 | BFF HA/LB | deferred | intentional | Medium |
| BFF-005 | command/read split | still needs tracking | contract gap | High |

---

## 11. Health / compose gap matrix v2

| ID | Area | Current | Gap Type | Severity |
|---|---|---|---|---|
| HLT-001 | shared helper /healthz /livez /readyz /metrics | present | progress | Low |
| HLT-002 | default compose | mostly /readyz | progress | Low |
| HLT-003 | control compose | still has `__health__` | cleanup | Medium |
| HLT-004 | exec compose | runtime-manager still has legacy health | cleanup | Medium |
| HLT-005 | staging control/exec split | present | progress | Medium |
| HLT-006 | production HA | deferred | intentional | Medium |

---

## 12. P0 revised gap list

```text
P0-001 Runtime maturity: complete pantheon-lean Launcher / RuntimeBootstrap / TelemetryEvent proof
P0-002 Keep blueprint and task packets canonicalized to pantheon/lean / pantheon-lean
P0-003 Keep CI guard: no P0 execution patch to lean-platform unless future migration ADR
P0-004 Formal DeploymentPlan → runtime_bootstrap contract
P0-005 Verify / implement RuntimeBinding context propagation
P0-006 Verify / implement paper runtime TelemetryEvent heartbeat
P0-007 Ensure live health-only sidecar is fail-closed
P0-008 Frontend auth: remove demo token path for staging/prod
P0-009 Frontend demo island cleanup for production routes
P0-010 Health endpoint cleanup in control/exec compose
```

---

## 13. P1 revised gap list

```text
P1-001 Bracket order guarded execution instead of log-only
P1-002 Full Lean Launcher production readiness plan
P1-003 Broker SDK activation criteria
P1-004 Canary runtime activation plan
P1-005 OpenClaw governed search integration
P1-006 Source/search external connector expansion: news/social/alpha DB
P1-007 Production Postgres ownership rollout
P1-008 BFF command/read contract cleanup
P1-009 Paper runtime reconciliation baseline
```

---

## 14. P2 revised gap list

```text
P2-001 BFF HA/LB if defer lifted
P2-002 Research OSS production activation
P2-003 Full OpenClaw paper/live adapter activation
P2-004 Advanced policy learning
P2-005 Full evolution action dispatcher
P2-006 Production incident/postmortem automation
```

---

## 15. 本章結論

SA-19 v2 的核心結論：

> **目前開發進度比原 SA 假設更具體、更成熟：foundation、default compose、source/search bounded baseline、OpenClaw facade、pantheon-lean bridge、paper runtime baseline 都已經存在。`pantheon/lean` / `pantheon-lean` 已是正式 execution bridge，不再作為 active gap 反覆盤點；真正剩餘差異是 runtime maturity 仍停在 paper baseline / live health-only / bounded bracket semantics，尚未到 production Lean Launcher + broker SDK live kernel。**
