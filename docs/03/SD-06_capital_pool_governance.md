# SD-06 — Capital Pool Governance / 資金池、風控政策與 Broker 邊界設計

版本：v0.1 Codex-ready draft  
適用範圍：Capital Pool Plane、Risk Policy Registry、Broker Account Registry、Persona-Capital Binding、Pool State Manager  
前置依賴：SD-00 Architecture Invariants、SD-01 Registry Backbone、SD-02 Persona Governance、SD-05 Consultation / Red-Team

---

## 1. Purpose

本文件定義 Pantheon 的 Capital Pool Governance。Capital pool 是正式治理物件，不是 deployment 的參數欄位。

此 plane 必須確保：

```text
persona authority
+ artifact eligibility
+ risk policy
+ broker capability
+ environment boundary
→ pool admissibility
→ deployment eligibility
```

任何 paper / canary / live runtime 都必須綁定 capital pool。persona 不能直接擁有 broker secret，也不能繞過 pool/risk policy 進入 execution。

---

## 2. Repo ownership

| Repo | Ownership |
|---|---|
| `pantheon` | Primary owner：capital pool registry、risk policy registry、broker account registry、persona-capital binding、pool state manager、admissibility checker。 |
| `front-ai-trading-system` | UI consumer：Capital Pool Console、Risk Policy Editor、Binding Viewer、Pool State View。 |
| `pantheon-lean` | Consumes broker/runtime config through approved RuntimeBinding only；does not own pool governance truth。 |
| `Lean` | Upstream reference only；not Pantheon governance authority。 |

---

## 3. Module paths

### `pantheon`

```text
services/capital-pool/
  __init__.py
  models.py
  commands.py
  queries.py
  events.py
  policies.py
  risk_policy.py
  broker_registry.py
  pool_registry.py
  binding_registry.py
  state_manager.py
  admissibility.py
  repository.py
  api.py
  tests/

docs/contracts/capital_pool.schema.json
docs/contracts/risk_policy.schema.json
docs/contracts/broker_account.schema.json
docs/contracts/persona_capital_binding.schema.json
docs/contracts/pool_admissibility_report.schema.json
docs/sd/06_capital_pool_governance.md
docs/codex/SD-06_task_packets.md
```

### `front-ai-trading-system`

```text
src/pages/capital-pools/*
src/pages/governance/PoolAdmissibilityPanel.tsx
src/types/capitalPool.ts
src/lib/capitalPoolClient.ts
```

### `pantheon-lean`

```text
# Required later by SD-08 integration; no direct governance ownership here.
Engine/Setup/*
Brokerages/*
```

---

## 4. Domain model

### 4.1 `CapitalPool`

```yaml
CapitalPool:
  capital_pool_id: string
  name: string
  desk: string
  base_currency: string
  environment: enum[dev, sandbox, paper, canary, live]
  status: enum[provisioned, paper_bound, canary_bound, live_bound, risk_off, paused, liquidating, archived]
  allowed_asset_classes: string[]
  allowed_strategy_families: string[]
  risk_policy_id: string
  broker_account_ref: string
  runtime_group: string
  created_by: actor_ref
  created_at: datetime
```

### 4.2 `RiskPolicy`

```yaml
RiskPolicy:
  risk_policy_id: string
  version: string
  name: string
  gross_limit: number
  net_limit: number
  max_single_name_weight: number
  max_sector_exposure: object | null
  max_factor_exposure: object | null
  max_leverage: number
  turnover_limit: number | null
  liquidity_constraints: object
  drawdown_actions:
    warn: number
    risk_off: number
    liquidate: number
  pause_rules: object
  liquidation_rules: object
  allowed_order_types: string[]
  allowed_time_in_force: string[]
  status: enum[draft, active, deprecated, retired]
```

### 4.3 `BrokerAccount`

```yaml
BrokerAccount:
  broker_account_ref: string
  broker_name: string
  account_alias: string
  environment: enum[paper, canary, live]
  supported_asset_classes: string[]
  supported_markets: string[]
  order_capabilities: string[]
  market_data_capabilities: string[]
  credential_ref: string
  status: enum[enabled, disabled, degraded]
  last_capability_check_at: datetime | null
```

### 4.4 `PersonaCapitalBinding`

```yaml
PersonaCapitalBinding:
  binding_id: string
  persona_id: string
  capital_pool_id: string
  role: enum[research_observer, paper_owner, canary_owner, live_owner, reviewer, operator]
  deployment_modes: enum[paper, canary, live][]
  mandate: string
  budget_limit: number | null
  risk_policy_override_ref: string | null
  effective_from: datetime
  effective_to: datetime | null
  status: enum[pending, active, suspended, expired, revoked]
```

### 4.5 `PoolAdmissibilityReport`

```yaml
PoolAdmissibilityReport:
  report_id: string
  capital_pool_id: string
  target_type: enum[candidate_artifact, deployment_plan, runtime_action]
  target_id: string
  result: enum[allowed, allowed_with_conditions, rejected]
  checks:
    - check_name: string
      status: enum[passed, warning, failed]
      reason: string
  blocking_reasons: string[]
  generated_at: datetime
  trace_id: string
```

### 4.6 `PoolStateSnapshot`

```yaml
PoolStateSnapshot:
  snapshot_id: string
  capital_pool_id: string
  state: string
  runtime_binding_id: string | null
  gross_exposure: number | null
  net_exposure: number | null
  cash: number | null
  positions_ref: string | null
  risk_summary: object
  observed_at: datetime
```

---

## 5. Commands

| Command | Input | Output | Notes |
|---|---|---|---|
| `RegisterCapitalPool` | pool payload | capital_pool_id | Admin/operator。 |
| `UpdateCapitalPool` | pool patch | updated pool | Restricted。 |
| `RegisterRiskPolicy` | policy payload | risk_policy_id | Requires validation。 |
| `ActivateRiskPolicy` | policy_id | active version | Only one active version per policy name/scope。 |
| `RegisterBrokerAccount` | broker payload | broker_account_ref | Stores secret ref only, not secret value。 |
| `CreatePersonaCapitalBinding` | binding payload | binding_id | Requires Persona capability check。 |
| `SuspendPersonaCapitalBinding` | binding_id | status=suspended | Audit required。 |
| `EvaluatePoolAdmissibility` | target + pool | report_id | Used by SD-07。 |
| `SetPoolRiskOff` | pool_id + reason | state=risk_off | May be system-triggered。 |
| `RequestPoolLiquidation` | pool_id + reason | liquidation request | High-risk action, requires policy/RBAC。 |

---

## 6. Queries

| Query | Output |
|---|---|
| `GetCapitalPool` | pool detail |
| `ListCapitalPools` | pools by status/environment |
| `GetRiskPolicy` | active or historical risk policy |
| `GetBrokerAccountCapabilities` | broker capabilities without secret values |
| `ListPersonaCapitalBindings` | bindings by persona/pool |
| `GetPoolAdmissibilityReport` | check results |
| `GetPoolStateSnapshot` | latest or historical state |
| `GetAllowedPoolsForArtifact` | eligible pools based on artifact + persona + policy |

---

## 7. Events

```yaml
CapitalPoolRegistered:
  capital_pool_id: string
  environment: string

RiskPolicyActivated:
  risk_policy_id: string
  version: string

BrokerAccountRegistered:
  broker_account_ref: string
  broker_name: string
  environment: string

PersonaCapitalBindingCreated:
  binding_id: string
  persona_id: string
  capital_pool_id: string

PoolAdmissibilityEvaluated:
  report_id: string
  capital_pool_id: string
  target_id: string
  result: string

CapitalPoolStateChanged:
  capital_pool_id: string
  from_state: string
  to_state: string
  reason: string

PoolRiskOffTriggered:
  capital_pool_id: string
  reason: string
  triggered_by: actor_ref
```

---

## 8. State machines

### 8.1 CapitalPool lifecycle

```text
provisioned → paper_bound → canary_bound → live_bound
live_bound → risk_off → paused → liquidating → archived
risk_off → live_bound  # only after recovery approval
```

### 8.2 RiskPolicy lifecycle

```text
draft → active → deprecated → retired
```

### 8.3 PersonaCapitalBinding lifecycle

```text
pending → active → suspended
active → expired
active → revoked
suspended → active  # only with approval
```

---

## 9. Hard invariants

1. Every deployment plan must reference a capital pool.
2. Every capital pool must reference exactly one active risk policy before binding to runtime.
3. Broker credentials are never returned by API; only `credential_ref` is visible.
4. Persona cannot deploy or operate a pool without active `PersonaCapitalBinding`.
5. Risk policy veto cannot be overridden by persona; only governance override can be recorded and audited.
6. Live pool cannot reuse paper/canary credential refs unless broker account explicitly supports isolated paper/live environments and policy permits it.
7. A pool in `risk_off`, `paused`, `liquidating`, or `archived` cannot accept new deployment plans.
8. Admissibility must check artifact strategy family, asset class, broker capability, risk policy, and persona binding.
9. Liquidation commands require high-risk RBAC and audit event.
10. Pool state transitions must be idempotent and traceable.

---

## 10. Policy hooks

| Policy | Purpose |
|---|---|
| `pool_admissibility_policy` | Determines if artifact/deployment can bind to pool。 |
| `risk_policy` | Hard risk limits and actions。 |
| `persona_capital_policy` | Maps persona lifecycle to allowed pool roles。 |
| `broker_capability_policy` | Validates order/asset/market support。 |
| `environment_segregation_policy` | Prevents paper/canary/live mixing。 |
| `risk_off_policy` | Determines automated risk-off trigger。 |
| `liquidation_policy` | Controls liquidation mode, approval, and ordering。 |

---

## 11. Storage model

```text
capital_pools
risk_policies
risk_policy_versions
broker_accounts
broker_capability_snapshots
persona_capital_bindings
pool_state_snapshots
pool_admissibility_reports
pool_audit_actions
```

Secret storage must be externalized:

```text
vault://broker/{broker_account_ref}/credentials
```

No plaintext broker secret should be persisted in Pantheon DB or frontend state.

---

## 12. API endpoints

```text
GET    /api/capital-pools
POST   /api/capital-pools
GET    /api/capital-pools/{pool_id}
PATCH  /api/capital-pools/{pool_id}
GET    /api/capital-pools/{pool_id}/state
POST   /api/capital-pools/{pool_id}/risk-off
POST   /api/capital-pools/{pool_id}/liquidation-request
GET    /api/risk-policies
POST   /api/risk-policies
GET    /api/risk-policies/{risk_policy_id}
POST   /api/risk-policies/{risk_policy_id}/activate
GET    /api/broker-accounts
POST   /api/broker-accounts
GET    /api/broker-accounts/{broker_account_ref}/capabilities
GET    /api/persona-capital-bindings
POST   /api/persona-capital-bindings
PATCH  /api/persona-capital-bindings/{binding_id}
POST   /api/capital-pools/{pool_id}/admissibility
GET    /api/capital-pools/admissibility/{report_id}
```

---

## 13. Integration points

| Integration | Direction | Contract |
|---|---|---|
| SD-02 Persona Governance | read | persona lifecycle and capability eligibility。 |
| SD-05 Consultation | read | required risk/committee memos for high-risk pool binding。 |
| SD-07 Promotion | read/write | deployment plan admissibility and approval inputs。 |
| SD-08 Execution | read | runtime consumes pool/broker refs after approved deployment plan。 |
| SD-09 Telemetry | write/read | pool state snapshots, risk-off events, exposure events。 |
| Console | read/command | capital pool UI and risk actions。 |

---

## 14. Tests

### Unit tests

- capital pool cannot be registered without environment.
- risk policy activation deprecates previous active version.
- broker account API never returns secret value.
- persona binding rejects invalid persona state.
- admissibility rejects incompatible asset class.
- admissibility rejects pool in risk_off.

### Integration tests

- approved artifact + active persona binding + compatible broker yields allowed report.
- incompatible broker capability yields rejected report.
- deployment plan request calls admissibility checker.
- risk-off state prevents new deployment.

### Security tests

- unauthorized user cannot create live binding.
- trainer role cannot request pool liquidation.
- broker credential_ref cannot be dereferenced from frontend API.

---

## 15. Definition of Done

1. Capital pool, risk policy, broker account, and persona binding registries exist.
2. Pool admissibility API returns deterministic reports.
3. Broker secrets are represented only by secret refs.
4. Pool state machine is enforced.
5. Promotion Plane can use admissibility result as a gate.
6. Frontend can inspect pools, risk policies, and binding status.
7. Tests cover risk veto, credential boundary, and environment segregation.

---

## 16. Codex task packets

### PTH-SD06-001 — Implement capital pool domain models

```text
Repo: ajoe734/pantheon
Target paths:
  services/capital-pool/models.py
  docs/contracts/capital_pool.schema.json
  docs/contracts/risk_policy.schema.json
  docs/contracts/broker_account.schema.json
Goal:
  Define CapitalPool, RiskPolicy, BrokerAccount, PersonaCapitalBinding, PoolAdmissibilityReport.
Acceptance tests:
  - BrokerAccount serializes credential_ref but never secret value
  - CapitalPool requires risk_policy_id before active binding
```

### PTH-SD06-002 — Implement pool state manager

```text
Repo: ajoe734/pantheon
Target paths:
  services/capital-pool/state_manager.py
  services/capital-pool/tests/test_state_manager.py
Goal:
  Enforce CapitalPool lifecycle transitions.
Acceptance tests:
  - provisioned -> paper_bound is allowed
  - live_bound -> risk_off is allowed
  - risk_off -> live_bound requires recovery approval flag
  - archived cannot transition back to active state
```

### PTH-SD06-003 — Implement admissibility checker

```text
Repo: ajoe734/pantheon
Target paths:
  services/capital-pool/admissibility.py
  services/capital-pool/tests/test_admissibility.py
Goal:
  Evaluate artifact/deployment compatibility with pool, risk policy, broker capability, and persona binding.
Acceptance tests:
  - incompatible asset class rejected
  - inactive persona binding rejected
  - pool in risk_off rejected
  - compatible inputs return allowed report
```

### PTH-SD06-004 — Implement capital pool APIs

```text
Repo: ajoe734/pantheon
Target paths:
  services/capital-pool/api.py
  services/capital-pool/repository.py
Goal:
  Expose CRUD/read APIs and admissibility endpoint.
Acceptance tests:
  - GET broker capabilities excludes secrets
  - POST admissibility returns report_id
  - state-changing commands emit audit/domain events
```
