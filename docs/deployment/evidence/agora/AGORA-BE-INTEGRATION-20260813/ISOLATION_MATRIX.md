# Multi-Tenant & Multi-User Isolation Matrix

- **Task**: `AGORA-BE-INTEGRATION-20260813`
- **Scope**: 2 Tenants (`tenant-alpha`, `tenant-beta`) $\times$ 2 Users (`user-1`, `user-2`)

---

## Component Isolation Matrix

| Component / Store | Scope Predicate | Cross-Tenant Read Result | Cross-User Read (Same Tenant) | Mutation Protection |
|---|---|---|---|---|
| **Identity Scope** | `tenant_id == identity.tenant_id` | `403 Forbidden` (Override blocked) | Scoped to caller user identity | Immutable scope token |
| **Strategy Workshop** | `(tenant_id, user_id)` | `KeyError` / `None` | Blocked / private | CAS `lock_version` check |
| **Strategy Version Links** | `(tenant_id, workshop_id)` | Filtered / `None` | Filtered / private | Write-once SHA-256 digest |
| **Research Plans** | `(tenant_id, user_id)` | Query returns `None` | Query returns `None` | State transition gate |
| **Candidate Pools** | `(tenant_id, user_id)` | Query returns `None` | Query returns `None` | Immutable artifact refs |
| **Trading Workspaces** | `(tenant_id, user_id)` | Query returns `None` | Query returns `None` | Dashboard versioning |
| **Decision Events** | `(tenant_id, strategy_id)` | Query returns `None` | Scoped to assigned strategy | Required `no_order_route_proof` |
| **Trading Intents** | `(tenant_id, decision_id)` | Query returns `None` | Scoped to authorized trader | `has_broker_order_authority=False` |
| **Performance Suggestions** | `(tenant_id, owner_user_id)` | Query returns `[]` | Query returns `[]` | CAS `expected_version` |
| **Dataset Extraction Inbox** | `(tenant_id, user_id)` | Query returns `None` | Query returns `None` | Dedupe on `(tenant, user, evid_id)` |
| **Policy Learning Backlog** | `(tenant_id, dedupe_key)` | Worker lease scoped | Scoped to tenant | Lease token expiration |
| **Consultation Requests** | `(tenant_id, request_id)` | Query returns `None` | Scoped to participant | Evaluator $\ne$ Producer gate |

---

## Key Invariant Verifications

1. **Strict 403 on Cross-Tenant Override**:
   Attempting to pass `requested_tenant_id="tenant-beta"` while authenticated as `tenant-alpha` is rejected at the identity resolution boundary.

2. **Durable Ledger Isolation**:
   In `PerformanceSuggestionStore`, the primary key and queries enforce `WHERE tenant_id=? AND owner_user_id=?`. Tenant Alpha User 2 cannot observe or act upon Tenant Alpha User 1's suggestions.

3. **Asynchronous Worker Tenant Binding**:
   Workers claiming candidates from the policy learning backlog must match `tenant_id`. Reclaims across tenant boundaries are strictly forbidden.

4. **Zero Shared In-Memory Leakage**:
   In-memory store maps use composite tuples `(tenant_id, user_id, entity_id)` or enforce tenant predicates prior to payload return.
