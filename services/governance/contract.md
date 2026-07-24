# Governance Service — API Contract

Last updated: 2026-07-13
Status: canonical API contract for BP5-SVC-003
Owner: Claude
Reviewer: Codex

---

## Purpose

This document is the authoritative contract for the `services/governance/`
deployable service.  It supersedes any previous ad-hoc governance API
descriptions in L3 supporting docs.

The governance service exposes the **ApprovalDecision** lifecycle as a
first-class deployable HTTP API.  Promotion, deployment-plan creation, and
evolution-decision follow-through flows **must** cite this service instead of
maintaining local approval fallbacks.

It also owns the canonical read models for **FreezeOrder** governance
quarantines and rollback request/outcome records.  Freeze and rollback remain
separate objects: a freeze changes future deployability, while a rollback is
an operational mitigation whose RuntimeBinding effects remain owned by the
Rollback Controller and Runtime Manager.

---

## Service Boundary

| Concern | Owner |
|---|---|
| ApprovalDecision propose / review / decide / revoke | **Governance Service** (this service) |
| Write-authority matrix (who may decide at which risk level) | **Governance Service** |
| Audit log (append-only, append on every state change) | **Governance Service** |
| FreezeOrder quarantine record | **Governance Service** |
| Rollback request/outcome read model | **Governance Service** |
| DeploymentPlan creation | `services/deployment/` (BP5-SVC-004) |
| RuntimeBinding writes | Execution Plane / Runtime Manager |
| EvolutionDecision lifecycle | `services/evolution/` (BP5-SVC-012) |

---

## Routes

### `POST /api/governance/approvals`
Propose a new ApprovalDecision.

**Request body** (`ProposeApprovalRequest`):
```json
{
  "decision_id":    "apv-<optional>",
  "target_type":    "registry_entry | strategy_spec | strategy_workshop | model_artifact | allocation_policy | persona_capital_binding | evolution_proposal",
  "target_id":      "<artifact or object ID>",
  "target_version": "<version string>",
  "risk_level":     "low | medium | high | critical",
  "capital_pool_id": "<optional>",
  "persona_id":      "<optional>"
}
```

**Response** (`201 Created`): `ApprovalDecisionResponse` with `decision_state = "proposed"`.

**Errors**:
- `409 Conflict` — `decision_id` already exists
- `422 Unprocessable Entity` — structural validation failure

---

### `GET /api/governance/approvals`
List decisions.  Supports filters: `target_type`, `target_id`,
`decision_state`, `risk_level`.  Returns most-recent first.

---

### `GET /api/governance/approvals/latest-approved`
Return the most-recent `decided` + `approved` decision for a target.

**Query params**: `target_type`, `target_id` (both required).

**Response**: `ApprovalDecisionResponse` or `null` (HTTP 200 either way).

This is the primary read path for downstream services that need to verify
canonical approval before creating a `DeploymentPlan` or executing an
evolution follow-through.

---

### `GET /api/governance/approvals/{decision_id}`
Retrieve a single decision by ID.

**Errors**: `404 Not Found`

---

### `GET /api/governance/freeze-orders`
List canonical FreezeOrder records.  Supports exact-match `status` and
`scope` filters and returns most-recent records first.  A healthy empty store
returns `200 []`.

Dev/local storage is `GOVERNANCE_DATA_DIR/freeze_orders.json`.  Enforced
Postgres posture uses `governance.freeze_orders` by default.

### `GET /api/governance/freeze-orders/{freeze_order_id}`
Retrieve one FreezeOrder record.  Returns `404` when it does not exist.

### `POST /api/governance/freeze-orders`
Record or update a canonical FreezeOrder record with audit fields.

**Request body**:
```json
{
  "freeze_order_id": "freeze-123",
  "status": "active",
  "scope": "persona",
  "target_id": "persona-alpha",
  "actor": "admin",
  "identity": "op-user",
  "source_command_id": "cmd-123",
  "reason": "Evolution sweep freeze."
}
```

### `GET /api/governance/rollbacks`
List canonical rollback request/outcome records.  Supports exact-match
`runtime_id`, `action_type`, and `status` filters and returns most-recent
records first.  A healthy empty store returns `200 []`.

This surface is an audit/read model.  It does not give governance authority
to modify RuntimeBinding or position lineage; those writes remain with the
Rollback Controller and Runtime Manager.

Dev/local storage is `GOVERNANCE_DATA_DIR/rollbacks.json`.  Enforced Postgres
posture uses `governance.rollbacks` by default.

### `GET /api/governance/rollbacks/{rollback_id}`
Retrieve one rollback record.  Returns `404` when it does not exist.

### `POST /api/governance/rollbacks`
Record or update a canonical Rollback record with audit fields.

**Request body**:
```json
{
  "rollback_id": "rollback-123",
  "runtime_id": "runtime-alpha",
  "action_type": "replace",
  "status": "completed",
  "actor": "reviewer",
  "identity": "op-user",
  "source_command_id": "cmd-123"
}
```

---

### `POST /api/governance/approvals/{decision_id}/review`
Accept review: `proposed → under_review`.

**Request body** (`AcceptReviewRequest`):
```json
{
  "actor_role": "<ActorRole>",
  "actor_id":   "<actor identifier>"
}
```

**Authorization**: `actor_role` must be permitted for the decision's
`risk_level` per the write-authority matrix.

**Errors**: `400 Bad Request` — wrong state or unauthorized role.

---

### `POST /api/governance/approvals/{decision_id}/decide`
Record outcome: `under_review → decided`.

**Request body** (`DecideRequest`):
```json
{
  "actor_role":     "<ActorRole>",
  "outcome":       "approved | rejected | approved_with_conditions",
  "rationale":     "<required>",
  "actor_id":      "<required actor identifier>",
  "conditions":    ["<required when outcome = approved_with_conditions>"],
  "evidence_refs": [{"ref_type": "...", "ref_id": "...", "storage_ref": {...}}]
}
```

**Authorization**: `actor_role` must be permitted for the decision's
`risk_level` per the write-authority matrix.

**Errors**: `400 Bad Request` — wrong state, unauthorized role, missing conditions, etc.

---

### `POST /api/governance/approvals/{decision_id}/revoke`
Revoke a decided decision.

**Request body** (`RevokeRequest`):
```json
{
  "actor_role": "risk_owner | governance_committee",
  "actor_id":   "<actor identifier>"
}
```

**Authorization**: only `risk_owner` and `governance_committee` may revoke.

**Errors**: `400 Bad Request` — wrong state or unauthorized role.

---

### `GET /api/governance/write-authority`
Return the write-authority matrix as a structured response.

**Response** (`WriteAuthorityResponse`):
```json
{
  "matrix": [
    {"risk_level": "low",      "authorized_roles": ["governance_reviewer", "automated_gate"], "revoke_roles": [...]},
    {"risk_level": "medium",   "authorized_roles": ["governance_reviewer", "risk_owner"],     "revoke_roles": [...]},
    {"risk_level": "high",     "authorized_roles": ["risk_owner", "governance_committee"],    "revoke_roles": [...]},
    {"risk_level": "critical", "authorized_roles": ["governance_committee"],                  "revoke_roles": [...]}
  ],
  "description": "..."
}
```

---

### `GET /api/governance/audit`
Return recent audit events.  Filtered by `decision_id` if provided.
Returns most-recent first.  Default `limit = 100`, max `1000`.

---

### `GET /health`
Liveness probe.  Returns `{"status": "ok", "service": "governance"}`.

---

## ApprovalDecision State Machine

```
proposed → under_review → decided → [revoked | superseded]
                        → canceled  (future)
```

Where `decided` carries a `decision` outcome of
`approved | rejected | approved_with_conditions`.

State transitions enforced by the service:
- `proposed → under_review` via `/review`
- `under_review → decided` via `/decide`
- `decided → revoked` via `/revoke`

---

## Write-Authority Matrix

| Risk level | Authorized roles (decide) | Revoke roles |
|---|---|---|
| `low`      | governance_reviewer, automated_gate  | risk_owner, governance_committee |
| `medium`   | governance_reviewer, risk_owner      | risk_owner, governance_committee |
| `high`     | risk_owner, governance_committee     | risk_owner, governance_committee |
| `critical` | governance_committee                 | risk_owner, governance_committee |

Source: `services/governance/write_authority.py`.
Platform-layer mirror: `services/control-plane/governance/approval_decision.py` (OWNER_MATRIX).

---

## Audit Events

Every state transition appends a JSON event to `$GOVERNANCE_DATA_DIR/audit.jsonl`
and (when `GCP_PROJECT_ID` is set) to Firestore collection `governance_audit`.

| Event type | When |
|---|---|
| `approval_decision_created`      | Decision proposed |
| `approval_decision_state_changed`| Review accepted |
| `approval_decision_decided`      | Outcome recorded |
| `approval_decision_revoked`      | Decision revoked |

---

## Storage

Dev/local posture stores each owned dataset in a separate JSON file under
`GOVERNANCE_DATA_DIR`:

| Dataset | JSON store | Postgres owner table |
|---|---|---|
| Approval decisions | `approval_decisions.json` | `governance.approval_decisions` |
| Freeze orders | `freeze_orders.json` | `governance.freeze_orders` |
| Rollback request/outcome records | `rollbacks.json` | `governance.rollbacks` |

`GOVERNANCE_STORE_BACKEND=json|postgres` selects the persistence posture for
all three datasets.  Postgres uses `GOVERNANCE_STORE_DSN` or `DATABASE_URL`;
the two new table names may be overridden with
`GOVERNANCE_FREEZE_ORDER_STORE_TABLE` and `GOVERNANCE_ROLLBACK_STORE_TABLE`.
The JSON files remain the dev rollback path, not a BFF-owned source of truth.

---

## Acceptance Criteria (BP5-SVC-003)

- [x] Approval objects are proposed, reviewed, decided, and revoked through
      one canonical HTTP API (`services/governance/main.py`)
- [x] Decision writes are enforced by the write-authority matrix; unauthorized
      roles receive HTTP 400
- [x] Every state transition is recorded in the append-only audit log
- [x] Downstream services (deployment planner, evolution controller) can call
      `GET /api/governance/approvals/latest-approved` as the single approval
      read path instead of maintaining local fallbacks
- [x] Unit tests cover the full lifecycle, authorization enforcement, revoke,
      and audit log growth (`test_governance_api.py`)
- [x] HTTP smoke test verifies all routes against a live server (`smoke_test.py`)

---

## Related L1 Policy Documents

- `BINDING_AND_DEPLOYMENT_SEMANTICS.md` — approval as prerequisite for DeploymentPlan
- `EVOLUTION_REVIEW_AND_THRESHOLDS.md` — approval authority by risk level and evolution type

---

## Dependencies

None.  `BP5-SVC-003` is a root task and does not depend on other Phase 5
service tasks.

Downstream tasks that depend on this service being complete:
- `BP5-SVC-004` — DeploymentPlan planner (cites approval_decision_id)
- `BP5-SVC-005` — Deployment orchestration saga
- `BP5-SVC-012` — EvolutionDecision governance read path
- `BP5-SVC-015` — BFF snapshot/fallback removal
- `BP5-WB-003`  — Governance Workbench packetization
