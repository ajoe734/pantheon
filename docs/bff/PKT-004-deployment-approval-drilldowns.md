# PKT-004 Module C — Deployment / Approval Drilldowns BFF Contract

## Purpose

Define the four read-only BFF surfaces for deployment plan and approval decision drilldowns linked from persona and binding journeys. Governance write actions and composed review screens remain with PKT-001.

## Read Routes

### DP-01 — Deployment Plan List

```
GET /api/v1/deployment-plans
Query params: status (optional), capital_pool_id (optional), page_token, page_size
```

Required response fields:

- `data[].id`
- `data[].plan_id`
- `data[].artifact_id`
- `data[].artifact_version`
- `data[].target_stage`
- `data[].current_stage`
- `data[].status`
- `data[].transition_type`
- `data[].capital_pool_id`
- `data[].binding_ids[]`
- `data[].approval_decision_id` (nullable)
- `meta.total`
- `meta.staleness` (nullable)

### DP-02 — Deployment Plan Detail

```
GET /api/v1/deployment-plans/{plan_id}
```

Required response fields:

- `data.id`
- `data.plan_id`
- `data.artifact_id`
- `data.artifact_version`
- `data.target_stage`
- `data.current_stage`
- `data.status`
- `data.transition_type`
- `data.capital_pool_id`
- `data.binding_ids[]`
- `data.runtime_binding_id` (nullable)
- `data.approval_decision_id` (nullable)
- `data.approval_decision` (embedded when available)
  - `id`
  - `outcome`
  - `state`
  - `reviewer`
  - `decided_at`
  - `risk_level`
- `meta.staleness` (nullable)

### DP-03 — Approval Decision List

```
GET /api/v1/approval-decisions
Query params: outcome (optional), state (optional), page_token, page_size
```

Required response fields:

- `data[].id`
- `data[].outcome`
- `data[].state`
- `data[].reviewer`
- `data[].decided_at`
- `data[].risk_level`
- `meta.total`
- `meta.staleness` (nullable)

### DP-04 — Approval Decision Detail

```
GET /api/v1/approval-decisions/{decision_id}
```

Required response fields:

- `data.id`
- `data.outcome`
- `data.state`
- `data.reviewer`
- `data.decided_at`
- `data.risk_level`
- `meta.staleness` (nullable)

## Design Rules

- All four surfaces are read-only. No write or command actions are defined in this module.
- Governance approval and review commands remain exclusively in PKT-001. Do not duplicate PKT-001 `allowedActions` patterns in these drilldown surfaces.
- Filters must be sent as query parameters — the BFF applies them. No client-side filtering.
- `viewer` role tokens are rejected.

## Non-Blocking Caveats

- `viewer` role is rejected on all DP surfaces.

## Cross-Reference

For composed governance review, approval commands, and deployment review CTAs, see:

- `docs/bff/PKT-001-deployment-review-console.md`
- `docs/bff/PKT-001-governance-review-queue.md`

## Example Payload

- `docs/examples/PKT-004-deployment-approval-drilldowns.json`
