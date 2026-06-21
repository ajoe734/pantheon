# Pantheon Agora — 系統設計團隊對 Open Design Gaps Round 2 的完整回覆

**日期：** 2026-06-21  
**回覆對象：** Agora 跨 Repo 開發團隊  
**基準：** `pantheon@dev` 最新已提交的 v1.2 additive contract、Round 2 gap inventory、`execute-plans@dev` Agora IA 決策  
**結論：** 開發團隊提出的 A–F 六組缺口判斷成立；這些不是 ops 問題，也不應由 worker 在實作時自行補語意。系統設計團隊決定以 **additive v1.3 design/contract bundle** 一次收斂。

---

## 1. 目前 dev 真實狀態

v1.2 已經完成：

- Workshop lifecycle/status 對齊；
- 私有內容 reference、redaction、storage/persistence；
- StrategySpec Registry reference mapping；
- Workshop tables/indexes；
- servant session_type；
- v1.2 OpenAPI and bundle index。

但 v1.2 的 `VersionCreateRequest.patch` 仍是任意 object；ResearchPlan/Run 只有基本 schema；Workshop SSE 仍沒有 typed event catalog；Trading Room 只有 UI shell/舊 TradingEvent/TradingIntent schema；Workshop cards 只有 component names；E2E 和 isolation sections 未形成可驗收 contract。

因此 Round 2 所列 A–F 必須新開 v1.3 additive layer。

---

## 2. 版本與檔案策略

### 不修改

```text
bundle_index.json
bundle_index.v1_1.json
bundle_index.v1_2.json
agora_v1.openapi.yaml
agora_v1_1.openapi.yaml
agora_v1_2.openapi.yaml
```

### 新增

```text
services/control-plane/specs/agora/v4/
  version_patch_proposal.schema.json
  version_compare.schema.json
  strategy_readiness.schema.json
  research_plan_execution.schema.json
  research_run_projection.schema.json
  workshop_stream_event.schema.json
  workshop_card.schema.json
  trading_room_aggregate.schema.json
  trading_decision_event.schema.json
  governed_intent_handoff.schema.json
  capability_manifest_v1_3.json

services/control-plane/openapi/agora_v1_3.openapi.yaml
services/control-plane/specs/agora/bundle_index.v1_3.json
```

`bundle_index.v1_3.json` extends the exact bytes of v1.2 and must be generated after merge.

---

# A. Strategy versioning / patch / readiness

## A1. Patch format decision

Use restricted RFC 6902 JSON Patch.

Allowed:

```text
add
remove
replace
test
```

Forbidden:

```text
move
copy
```

Allowed StrategySpec roots:

```text
/title
/hypothesis
/objective
/market_scope
/data_dependencies
/execution_profile
/evaluation_plan
/governance
/evidence_refs
/code_refs
/metadata
```

Immutable/system-owned:

```text
/spec_version
/strategy_id
/lifecycle_state
/provenance
```

## A2. Patch application

```text
resolve exact base Registry version
→ verify base SHA-256 and workshop scope
→ validate paths
→ apply to in-memory copy
→ validate canonical StrategySpec schema
→ validate policy
→ create new immutable Registry draft
→ create workshop-version link
```

No Registry row is updated in place.

## A3. VersionPatchProposal

Required identity:

```text
proposal_id
workshop_id
strategy_id
base_workshop_version_id
base_strategy_spec_registry_id
base_document_sha256
proposed_by
source_event_ids
operations
rationale
status
```

Lifecycle:

```text
draft → validating → validated → accepted
                    ↘ invalid
validated → rejected
draft/validated → superseded
```

## A4. Version comparison

One base + up to four candidate versions.

Separate:

```text
field diffs
risk diffs
readiness diffs
predicted effects
in-sample results
OOS results
paper-observed results
```

A predicted effect must never be rendered as an observed result. The servant may recommend; the trader remains decision authority.

## A5. Three readiness gates

### Preliminary research

Must define/test:

- hypothesis/objective;
- market scope;
- data/PIT posture;
- candidate/signal logic;
- entry/exit/invalidation;
- evaluation;
- no critical conflict.

A temporary assumption may create `conditional`, not `ready`.

### Full validation

Requires:

- real/governed historical prototype;
- rolling/walk-forward OOS;
- cost/slippage;
- liquidity/capacity;
- parameter robustness;
- regime/subperiod;
- sizing/risk;
- PIT/look-ahead/survivorship;
- required consult/red-team;
- fixed selected Registry version.

Fixture/stub cannot satisfy this gate.

### Trading Room

Requires:

- full validation ready;
- selected version;
- candidate/scoring recipe;
- entry/add/reduce/exit/review rules;
- sizing/leverage/risk budget;
- accepted dashboard recipe;
- shadow/paper posture;
- governed intent handoff;
- no-order-route proof;
- monitoring freshness/invalidation.

Gate state:

```text
not_assessed
blocked
conditional
ready
stale
```

---

# B. Research facade + run projection

## B1. Plan-first

Every research run must be tied to a persisted ResearchPlan.

Base lifecycle remains:

```text
draft
approved
running
completed
cancelled
```

A rejected approval is represented as `cancelled` with `terminal_reason=approval_rejected`, preserving frozen schema compatibility.

## B2. Canonical routes

```text
GET/POST /bff/agora/workshops/{workshop_id}/research-plans
GET      /bff/agora/research-plans/{plan_id}
POST     /bff/agora/research-plans/{plan_id}/approve
POST     /bff/agora/research-plans/{plan_id}/cancel
GET/POST /bff/agora/research-plans/{plan_id}/runs
GET      /bff/agora/research-runs/{run_id}
POST     /bff/agora/research-runs/{run_id}/cancel
GET      /bff/agora/research-runs/{run_id}/artifacts
```

Legacy workshop `research-runs` cannot bypass plan creation/approval.

## B3. Stage routing

```text
source_discovery             → governed source ingestion
data_validation              → data registry/validator
prototype_backtest           → vectorbt
alpha_training               → Qlib
rolling_oos                  → Qlib
econometric_validation       → statsmodels
derivatives_pricing_risk     → QuantLib
policy_training              → FinRL/RLlib, activation-gated
parameter_search             → Ray Tune
portfolio_synthesis          → existing optimizer-svc
robustness_stress            → orchestrated backend set
evidence_synthesis           → OpenClaw result-synthesis skill
```

The LLM proposes stage intent. Route policy resolves the backend. No arbitrary tool names and no silent stub fallback.

## B4. ResearchRunProjection

Must include:

- plan/workshop/strategy/version/stage lineage;
- requested/effective backend;
- real/fixture/stub mode;
- queued/dispatching/running/succeeded/failed/cancelled/timed_out;
- progress;
- outcome;
- metrics by performance/risk/cost/capacity/robustness/calibration/data-quality;
- findings/warnings/blockers/failure;
- artifact/evidence/lineage refs;
- data cutoff;
- no-order-route proof.

---

# C. Workshop SSE

## C1. Typed envelope

Every event includes:

```text
event_id
event_type
aggregate_type
aggregate_id
sequence_no
causal_parent_id
event_time
emitted_at
trace_id
request_id
idempotency_key
data_cutoff
visibility
payload_schema
payload
```

## C2. Event catalog

```text
workshop.snapshot
workshop.message.accepted
workshop.servant.response.started/delta/completed
workshop.completeness.updated
workshop.next_question.updated
workshop.patch.proposed/validated
workshop.version.created/selected
workshop.readiness.updated
research.plan.created/approved/cancelled
research.run.queued/progress/completed/failed
consultation.started/completed
workshop.concluded/archived
stream.heartbeat
stream.error
```

## C3. Latency

- POST message writes/redacts/encrypts/persists then returns command receipt.
- p95 first persisted acknowledgement target <2 seconds.
- LLM/research completion is asynchronous.
- SSE progress is not tied to the POST connection lifetime.

## C4. Ordering/replay

- per-workshop monotonic sequence;
- at-least-once;
- dedupe by event ID;
- Last-Event-ID replay;
- replay window ≥24h or 10,000 events/workshop;
- heartbeat 15s;
- degraded after 45s silence;
- missing replay returns `SSE_REPLAY_UNAVAILABLE`, then client reloads snapshot.

---

# D. Trading Room + governed intent

## D1. Trading Room responsibilities

It presents:

- strategy-specific dashboards;
- candidate and position monitoring;
- entry/add/reduce/exit/review queues;
- probability/EV/risk/evidence/invalidation;
- trader decisions;
- shadow/paper/canary/live review requests.

It does not own orders, capital or RuntimeBinding.

## D2. Decision-event fields

```text
event kind
origin
strategy/version
subject/candidate/position
trigger and distance
confidence + calibration
probability + horizon + interval
gross/cost/net EV + downside
structured rationale
risk notes
evidence refs
invalidation
non-binding suggested action/size
position snapshot
freshness/data cutoff
no-order-route proof
```

Confidence and probability are different fields.

## D3. Decision lifecycle

```text
approaching
→ triggered
→ pending_review
→ decided

approaching/triggered → invalidated
pending_review → expired/superseded
```

## D4. Trader decisions

```text
approve
reject
defer
modify
```

Approve/modify creates a TradingIntent, not an order.

## D5. Governed handoff

```text
shadow → no-order research/shadow path
paper → Management/governance validation request
canary/live → promotion-review request only
```

Only existing Governance/DeploymentPlan/RuntimeBinding/LEAN paths may produce execution.

---

# E. Workshop card contracts

Cards share a typed envelope and bind from BFF projections, not arbitrary LLM markdown.

Required cards:

```text
user_strategy_description
servant_reconstruction
completeness_update
missing_definition
next_question
research_plan_proposal
research_progress
research_result
consult_result
version_patch_proposal
version_compare
readiness_gate
```

Each has field-level payload definitions in `05_workshop_card_contracts.md`.

The frontend may render markdown inside a typed field, but cannot infer card type/meaning by parsing free-form assistant output.

---

# F. E2E + isolation

## F1. Winner-branch flow

1. ensure private servant;
2. create workshop with expert winner-branch hypothesis;
3. reconstruct/gap/next question;
4. create first StrategySpec Registry draft and workshop link;
5. propose/approve ResearchPlan;
6. execute governed stages with progress/evidence;
7. produce/accept patch proposal;
8. compare versions and reach evidence-based readiness;
9. select one or more execution candidates;
10. build candidate pool + dashboard + Trading Room;
11. record decision/TradingIntent and send shadow or request-only governed handoff.

At no point may Agora route an order.

## F2. Isolation classes

The acceptance matrix covers:

- exact cross-repo contract/hash compatibility;
- User A vs User B workshop/private content/dashboard/candidate/intent/SSE isolation;
- Agora token vs Management command routes;
- Management redacted-only projection;
- central persona minimized ContextBundle;
- no capital/runtime/broker authority;
- private storage/redaction/retention;
- idempotency/concurrency/event replay.

---

## 3. Required v1.3 implementation tasks

```text
AG-DES-VERS-001
AG-DES-RS-001
AG-DES-SSE-001
AG-DES-TR-001
AG-DES-CARD-001
AG-DES-E2E-001
AG-XR-OPENAPI-004
```

Only after `AG-XR-OPENAPI-004` merges the exact schemas/OpenAPI/capability manifest/bundle hashes may downstream workers resume.

---

## 4. Unblock mapping

| Task | Needs |
|---|---|
| AG-BE-SW-002 | VERS |
| AG-FE-SW-003 | VERS + CARD |
| AG-BE-RS-004 | VERS + RS |
| AG-FE-RS-001 | VERS + RS + CARD |
| AG-BE-RS-001/002 | RS |
| AG-BE-SW-004 | SSE |
| AG-BE-TR-001/002 | TR |
| AG-FE-TR-001/002 | TR + CARD |
| AG-FE-SW-001/002 | CARD + SSE |
| AG-E2E-SW-001 | E2E + isolation |
| AG-E2E-TR-001 | TR E2E |
| AG-TEST-ID-001 | isolation |

`AG-FE-DB-002` remains a delivery/sync issue, not a v1.3 design blocker.

---

## 5. Definition of Done for the design response

The Round 2 design gaps are closed only when:

1. all prose files are merged;
2. all v4 schemas validate;
3. OpenAPI v1.3 includes every route and typed response;
4. capability manifest v1.3 is merged;
5. bundle index v1.3 hashes exact merged bytes;
6. frontend generated types are produced from v1.3;
7. task briefs cite actual merged paths, not missing SD section numbers;
8. contract tests and E2E tests reference the same schema versions.
