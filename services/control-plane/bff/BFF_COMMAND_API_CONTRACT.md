# BFF Command API Contract (v1)

Last updated: 2026-05-14
Status: canonical - governed BFF command facade contract for P0 command admission
Tier: L2 Planning & Execution (paired with `BFF_API_CONTRACT.md`)
Scope: command admission, receipt, idempotency, RBAC/policy, trace, and audit requirements for BFF-routed operator commands
Owner: Codex2
Reviewer: Claude
Task-ID: P0-BFF-CMD-001

---

## 1. Purpose

This document separates the BFF command facade from the read-oriented BFF API
contract.

The read contract remains GET-only and is defined in `BFF_API_CONTRACT.md`.
This command contract defines the narrow write-facing facade used by operator
surfaces to request governed actions. The BFF accepts command envelopes, runs
admission controls, records command/audit/idempotency state, and dispatches to
canonical control-plane authorities. The BFF does not become a canonical store.

## 2. Boundary Rules

1. Read surfaces must not be reused as pseudo-write channels.
2. Command admission must not silently mutate UI-only state as canonical truth.
3. Runtime, deployment, approval, rollback, kill-switch, incident, and evolution
   actions must enter through a command envelope or a documented secondary
   control path.
4. BFF command records are command/audit records. Domain state transitions remain
   owned by the downstream governance, runtime-manager, incident, or execution
   authority.
5. Control-plane degradation does not relax RBAC, idempotency, MFA, or audit
   requirements.

## 3. Routes

| Route | Method | Purpose |
|---|---:|---|
| `/bff/v1/commands` | POST | Submit a governed operator command (final contract); returns `CommandResponse<T>`. |
| `/bff/actions/{entityType}/{entityId}/{actionId}` | POST | Deprecated generic action adapter; dual-writes through final command admission and returns a deprecated receipt marker. |
| `/api/v1/operator/commands` | POST | Legacy command submission; returns `CommandSubmissionResponse`. Kept for adapter compatibility. |
| `/api/v1/operator/commands/{command_id}` | GET | Poll command status, result, error, and audit record. |

The status route is a read projection of command state. It is not a retry or
mutation endpoint.

### Final vs Legacy Route

The final contract route `/bff/v1/commands` is the authoritative command surface for new
frontend integrations. As of 2026-05-14, frontend `runAction()` live writes default to this
route. The generic `/bff/actions/*` adapter remains active only for explicit compatibility
checks and old clients; it must expose deprecation metadata rather than silently behaving as
the preferred path. The legacy `/api/v1/operator/commands` remains active to avoid breaking
existing adapters and must not be silently removed; use an explicit migration test when
retiring it.

Key differences:

| Dimension | `/bff/v1/commands` (final) | `/api/v1/operator/commands` (legacy) |
|---|---|---|
| Idempotency header | `Idempotency-Key` (canonical); `X-Idempotency-Key` accepted as alias | `X-Idempotency-Key` only |
| Body `idempotencyKey` | Rejected with 400 `INVALID_REQUEST` | Not checked |
| Response shape | `CommandResponse<T>` with `status` and `data` | `CommandSubmissionResponse` with flat `receipt_id` |

### Deprecated Generic Action Adapter

`POST /bff/actions/{entityType}/{entityId}/{actionId}` is deprecated as of 2026-05-14.
It is retained as a compatibility adapter until at least 2026-06-15 while downstream audit
and replay tooling finishes consuming the final command receipt.

Compatibility responses must still be successful `CommandResponse<T>` envelopes on accepted
commands, but they also include:

- HTTP headers: `Deprecation: true`, `Sunset: Mon, 15 Jun 2026 00:00:00 GMT`,
  `Link: </bff/v1/commands>; rel="successor-version"`, `X-Pantheon-Deprecated-Route:
  /bff/actions/*`, and a 299 `Warning` naming `/bff/v1/commands`.
- `data.deprecated: true` and `data.deprecation.replacement: "/bff/v1/commands"`.
- `data.receipt.deprecated: true` for consumers still reading the nested receipt.
- `meta.deprecated: true` plus the same `meta.deprecation` object for audit and replay
  tools that inspect metadata before receipt bodies.

## 4. Required Admission Controls

Every accepted command must persist:

| Control | Contract |
|---|---|
| Actor | `actor_ref` from authenticated operator identity; anonymous commands are rejected. |
| Trace | non-empty `trace_id` and `correlation_id`; `X-Trace-Id` may be supplied by caller, otherwise BFF generates one. |
| Idempotency | non-empty idempotency key from header; duplicate key with same request returns the original receipt; same key with different request returns conflict. On final routes, `Idempotency-Key` is canonical and `X-Idempotency-Key` is a temporary compatibility alias; body-level `idempotencyKey` is rejected. |
| RBAC / policy | command-specific validator must produce a policy decision (`allow` or `deny`) tied to actor, target, action, environment, and trace. |
| Audit | non-empty `audit_context.reason`; accepted, denied, validation-failed, and idempotency-conflict commands emit an audit action. |
| Target | typed target reference (`target.type`, `target.id`) matching the command class. |

## 5. Request Shape

Headers (final `/bff/v1/commands` route):

```http
Authorization: Bearer <operator-token>
Idempotency-Key: <stable-client-retry-key>
X-Idempotency-Key: <compatibility alias — accepted when Idempotency-Key is absent>
X-Trace-Id: <optional-trace-id>
X-Correlation-Id: <optional-correlation-id>
X-Request-Id: <optional-request-id>
X-MFA-Token: <required for MFA-gated commands when not already session-bound>
```

`Idempotency-Key` takes precedence over `X-Idempotency-Key` when both are present.
`idempotencyKey` in the request body is rejected with 400 `INVALID_REQUEST` on final routes.

Headers (legacy `/api/v1/operator/commands` route):

```http
Authorization: Bearer <operator-token>
X-Idempotency-Key: <stable-client-retry-key>
X-Trace-Id: <optional-trace-id>
```

Body:

```json
{
  "command": "ApproveDecision",
  "target": {
    "type": "ApprovalDecision",
    "id": "appr-001"
  },
  "action": "approve",
  "params": {
    "decision_id": "appr-001"
  },
  "audit_context": {
    "reason": "Policy checks passed",
    "incident_id": null
  }
}
```

The BFF persists a foundation command context containing:

```json
{
  "command_envelope": {
    "command_id": "cmd-...",
    "command_type": "ApproveDecision",
    "actor_ref": { "actor_type": "user", "actor_id": "op-6", "roles": ["approver"] },
    "idempotency_key": "idmp-...",
    "trace": { "trace_id": "trace-...", "correlation_id": "trace-..." }
  },
  "idempotency_record": { "idempotency_key": "idmp-...", "status": "succeeded" },
  "policy_decision": { "decision": "allow" },
  "audit_action": { "action_type": "bff.command.accepted" }
}
```

## 6. Response Shape

Accepted commands return HTTP 202:

```json
{
  "receipt_id": "cmd-...",
  "command": "ApproveDecision",
  "status": "accepted",
  "accepted_at": "2026-05-01T00:00:00Z",
  "routing_path": "direct",
  "expected_completion_at": "2026-05-01T00:00:02Z"
}
```

Final frontend-facing command routes use:

```json
{
  "status": "accepted",
  "data": {
    "receipt_id": "cmd-...",
    "command": "ApproveDecision"
  },
  "meta": {}
}
```

Contract rules:

- `CommandResponse<T>.data` is required.
- `ActionCommandStatus` is exactly `accepted`, `queued`, or `completed`.
- `requires_approval`, `requires_confirm_token`, and `requires_two_man` are
  not success statuses. Missing preconditions must be returned as non-2xx
  errors.
- The legacy `/api/v1/operator/commands` response remains
  `CommandSubmissionResponse` until that route is explicitly migrated; new
  final-contract routes should use the final `CommandResponse<T>` adapter.

Rejected commands return a non-2xx `BffErrorEnvelope` plus foundation error,
policy decision when applicable, and audit action evidence. Canonical BFF error
codes include:

| Code | Intended Use |
|---|---|
| `CONFIRM_TOKEN_REQUIRED` | Operator confirmation token is missing or expired. |
| `APPROVAL_REQUIRED` | Required approval evidence is absent. |
| `TWO_MAN_REQUIRED` | A second authorized operator decision/signature is required. |
| `IDEMPOTENCY_CONFLICT` | Same idempotency key was reused with a different payload. |
| `SSE_REPLAY_UNAVAILABLE` | Requested SSE replay window is no longer available. |

## 7. Command Classes

| Class | Commands | Minimum Admission Contract |
|---|---|---|
| Deployment | `ApproveDeployment`, `EscalateDiff` | approver/admin or governance operator role; deployment target; audit reason; idempotency key. |
| Approval | `ApproveDecision`, `RejectDecision`, `RequestApprovalRevision`, `ApproveRollback`, `RejectRollback` | approver/admin role; approval or rollback target; policy decision and audit action. |
| Runtime | `PauseRuntime`, `PauseExecution`, `ExecuteRollback`, `HardRollback` | operator/admin/approver role per action; runtime or runtime-binding target; live broker scope fail-closed when disabled. |
| Incident / kill switch | `IssueRiskOff`, `LiquidateAll`, `IssueSafeMode`, `ActivateKillSwitch` | operator/admin role per action; admin+MFA for destructive commands; audit reason and command receipt. |
| Evolution / governance | `ApproveEvolutionDecision`, `ExecuteEvolutionAction`, `ApproveMutation`, `RejectMutation`, `RecordSponsorDecision` | policy-gated governance role; target state checked against read projection before dispatch. |

## 8. Command Adapter Mapping

This section maps every `/bff/actions/{entityType}/{entityId}/{actionId}` call that
`runAction.ts` emits (plus special-path decision writes and confirm-token lifecycle
calls) to the equivalent `/bff/v1/commands` envelope fields required by
BFF-CONSOL-019's command adapter implementation.

Sources for the action vocabulary:
- `execute-plans/src/lib/bff/runAction.ts` `KIND_TO_ENTITY_TYPE` and `paths.action()`
- `execute-plans/src/lib/v3/highRiskActions.ts` `HIGH_RISK_ACTIONS`
- `execute-plans/src/lib/stateMachines/index.ts` (state-machine catalogs)
- `execute-plans/src/lib/bff/mutations.ts` (domain mutation helpers)

**Column key**

| Column | Meaning |
|---|---|
| `action_id` | Value of the `{actionId}` path segment in `/bff/actions/…` (matches `input.action` in `RunActionInput`). |
| `target_type` | Value of `{entityType}` in the BFF action path; also becomes `target.type` in the command envelope. |
| `command_name` | Value of the `command` field submitted to `/bff/v1/commands`. |
| `idempotency_key_template` | Pattern for the `Idempotency-Key` header; `{entityId}` and `{idemKey}` are placeholders. The frontend-minted key is passed through as-is; this template documents the expected structure for backend idempotency records. |
| `actor_source` | Where `actor_ref` is extracted — always from the authenticated session. |
| `trace_propagation` | Headers the BFF adapter must forward from the incoming request to the downstream command submission. |
| `audit_event` | Audit event name written by the backend on admission. Follows `{domain}.{action}` convention and aligns with the existing mock `pushAudit` calls in `mutations.ts`. |
| `policy_check` | Minimum RBAC / policy gate before admission. Role values use the canonical backend vocabulary (viewer / operator / approver / admin). |

**Source and audit semantics**

- Rows in §8.1-§8.16 are generic `runAction` route mappings. Their `audit_event`
  values follow `mutations.runAction()` exactly: `${kind.toLowerCase()}.${action}`.
  This is intentionally different from camelCase typed helpers such as
  `setAllocationLimit()` or `rotateMcpSecret()`.
- Rows marked "active caller" are emitted by current `runActionSafe()` call sites
  in execute-plans detail pages/components, not only by state-machine transition
  catalogs.
- Confirm-token policy uses the v3 high-risk action catalog. When a current caller
  mints a token with a catalog id that differs from the route `action_id`, the
  required catalog id is named in `policy_check`.

### 8.1 Strategy Actions

Route template: `POST /bff/actions/strategy/{strategyId}/{actionId}`

| action_id | target_type | command_name | idempotency_key_template | actor_source | trace_propagation | audit_event | policy_check |
|---|---|---|---|---|---|---|---|
| `scaffold_spec` | `strategy` | `ScaffoldStrategySpec` | `strategy:{entityId}:scaffold_spec:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `strategy.scaffold_spec` | operator |
| `run_replication` | `strategy` | `RunStrategyReplication` | `strategy:{entityId}:run_replication:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `strategy.run_replication` | operator |
| `submit_review` | `strategy` | `SubmitStrategyReview` | `strategy:{entityId}:submit_review:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `strategy.submit_review` | operator |
| `promote_paper` | `strategy` | `PromoteStrategyPaper` | `strategy:{entityId}:promote_paper:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `strategy.promote_paper` | approver; confirm-token required using `strategy.promote_paper` |
| `promote_live` | `strategy` | `PromoteStrategyLive` | `strategy:{entityId}:promote_live:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `strategy.promote_live` | approver; confirm-token required using `strategy.deploy_live`; two-man optional |
| `mark_degraded` | `strategy` | `MarkStrategyDegraded` | `strategy:{entityId}:mark_degraded:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `strategy.mark_degraded` | operator |
| `replace_strategy` | `strategy` | `ReplaceStrategy` | `strategy:{entityId}:replace_strategy:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `strategy.replace_strategy` | approver; confirm-token required using `strategy.deploy_live` |
| `retire_live` | `strategy` | `RetireStrategy` | `strategy:{entityId}:retire_live:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `strategy.retire_live` | approver; confirm-token required using `strategy.retire` |
| `rollback_to_paper` | `strategy` | `RollbackStrategyToPaper` | `strategy:{entityId}:rollback_to_paper:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `strategy.rollback_to_paper` | approver; confirm-token required using `strategy.rollback_live` |
| `archive` | `strategy` | `ArchiveStrategy` | `strategy:{entityId}:archive:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `strategy.archive` | admin; confirm-token required |
| `emergency_kill` | `strategy` | `EmergencyKillStrategy` | `strategy:{entityId}:emergency_kill:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `strategy.emergency_kill` | admin or risk_officer; confirm-token TTL=120s; no pre-approval |
| `update_params` | `strategy` | `UpdateStrategyParams` | `strategy:{entityId}:update_params:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `strategy.update_params` | operator; active caller |
| `lock_params` | `strategy` | `LockStrategyParams` | `strategy:{entityId}:lock_params:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `strategy.lock_params` | operator |
| `unlock_params` | `strategy` | `UnlockStrategyParams` | `strategy:{entityId}:unlock_params:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `strategy.unlock_params` | operator |
| `run_sweep` | `strategy` | `RunStrategyParameterSweep` | `strategy:{entityId}:run_sweep:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `strategy.run_sweep` | operator |

### 8.2 Persona Actions

Route template: `POST /bff/actions/persona/{personaId}/{actionId}`

| action_id | target_type | command_name | idempotency_key_template | actor_source | trace_propagation | audit_event | policy_check |
|---|---|---|---|---|---|---|---|
| `create_sandbox` | `persona` | `CreatePersonaSandbox` | `persona:{entityId}:create_sandbox:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `persona.create_sandbox` | operator |
| `activate_persona` | `persona` | `ActivatePersona` | `persona:{entityId}:activate_persona:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `persona.activate_persona` | approver; confirm-token required using `persona.activate` |
| `put_on_probation` | `persona` | `PutPersonaOnProbation` | `persona:{entityId}:put_on_probation:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `persona.put_on_probation` | operator |
| `restrict_persona` | `persona` | `RestrictPersona` | `persona:{entityId}:restrict_persona:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `persona.restrict_persona` | approver; confirm-token required using `persona.restrict` |
| `suspend_persona` | `persona` | `SuspendPersona` | `persona:{entityId}:suspend_persona:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `persona.suspend_persona` | approver; confirm-token required using `persona.suspend` |
| `restore_active` | `persona` | `RestorePersonaActive` | `persona:{entityId}:restore_active:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `persona.restore_active` | approver |
| `remove_restriction` | `persona` | `RemovePersonaRestriction` | `persona:{entityId}:remove_restriction:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `persona.remove_restriction` | approver |
| `retire_persona` | `persona` | `RetirePersona` | `persona:{entityId}:retire_persona:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `persona.retire_persona` | approver; confirm-token required |
| `archive_persona` | `persona` | `ArchivePersona` | `persona:{entityId}:archive_persona:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `persona.archive_persona` | admin; confirm-token required |
| `update_route_policy` | `persona` | `UpdatePersonaRoutePolicy` | `persona:{entityId}:update_route_policy:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `persona.update_route_policy` | approver; confirm-token required using `persona.update_route_policy` |
| `test` | `persona` | `TestPersona` | `persona:{entityId}:test:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `persona.test` | operator |
| `run_eval` | `persona` | `RunPersonaEval` | `persona:{entityId}:run_eval:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `persona.run_eval` | operator |
| `restrict_tools` | `persona` | `RestrictPersonaTools` | `persona:{entityId}:restrict_tools:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `persona.restrict_tools` | approver |

### 8.3 Capital Pool Actions

Route template: `POST /bff/actions/capital-pool/{poolId}/{actionId}`

| action_id | target_type | command_name | idempotency_key_template | actor_source | trace_propagation | audit_event | policy_check |
|---|---|---|---|---|---|---|---|
| `activate_pool` | `capital-pool` | `ActivateCapitalPool` | `capital-pool:{entityId}:activate_pool:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `capitalpool.activate_pool` | approver |
| `freeze_pool` | `capital-pool` | `FreezeCapitalPool` | `capital-pool:{entityId}:freeze_pool:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `capitalpool.freeze_pool` | approver; confirm-token required using `capital_pool.freeze` |
| `unfreeze_pool` | `capital-pool` | `UnfreezeCapitalPool` | `capital-pool:{entityId}:unfreeze_pool:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `capitalpool.unfreeze_pool` | approver |
| `start_rebalance` | `capital-pool` | `StartCapitalPoolRebalance` | `capital-pool:{entityId}:start_rebalance:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `capitalpool.start_rebalance` | approver |
| `apply_rebalance` | `capital-pool` | `ApplyCapitalPoolRebalance` | `capital-pool:{entityId}:apply_rebalance:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `capitalpool.apply_rebalance` | approver |
| `restrict_pool` | `capital-pool` | `RestrictCapitalPool` | `capital-pool:{entityId}:restrict_pool:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `capitalpool.restrict_pool` | approver |
| `retire_pool` | `capital-pool` | `RetireCapitalPool` | `capital-pool:{entityId}:retire_pool:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `capitalpool.retire_pool` | admin; confirm-token required |
| `edit_mandate` | `capital-pool` | `EditCapitalPoolMandate` | `capital-pool:{entityId}:edit_mandate:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `capitalpool.edit_mandate` | approver; confirm-token required using `capital_pool.edit_mandate` |
| `set_risk_budget` | `capital-pool` | `SetCapitalPoolRiskBudget` | `capital-pool:{entityId}:set_risk_budget:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `capitalpool.set_risk_budget` | approver; confirm-token required using `capital_pool.set_risk_budget` |
| `adjust_budget` | `capital-pool` | `AdjustCapitalPoolBudget` | `capital-pool:{entityId}:adjust_budget:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `capitalpool.adjust_budget` | approver; confirm-token required using `capital_pool.set_risk_budget`; active caller |
| `set_limit` | `capital-pool` | `SetCapitalPoolAllocationLimit` | `capital-pool:{entityId}:set_limit:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `capitalpool.set_limit` | approver |

### 8.4 Rebalance Actions

Route template: `POST /bff/actions/rebalance/{rebalanceId}/{actionId}`

| action_id | target_type | command_name | idempotency_key_template | actor_source | trace_propagation | audit_event | policy_check |
|---|---|---|---|---|---|---|---|
| `start_metrics_freeze` | `rebalance` | `StartRebalanceMetricsFreeze` | `rebalance:{entityId}:start_metrics_freeze:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `rebalance.start_metrics_freeze` | operator |
| `metrics_frozen` | `rebalance` | `MarkRebalanceMetricsFrozen` | `rebalance:{entityId}:metrics_frozen:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `rebalance.metrics_frozen` | operator |
| `unfreeze_metrics` | `rebalance` | `UnfreezeRebalanceMetrics` | `rebalance:{entityId}:unfreeze_metrics:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `rebalance.unfreeze_metrics` | approver |
| `calculate_ranking` | `rebalance` | `CalculateRebalanceRanking` | `rebalance:{entityId}:calculate_ranking:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `rebalance.calculate_ranking` | operator |
| `run_simulation` | `rebalance` | `RunRebalanceSimulation` | `rebalance:{entityId}:run_simulation:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `rebalance.run_simulation` | operator |
| `submit_for_review` | `rebalance` | `SubmitRebalanceForReview` | `rebalance:{entityId}:submit_for_review:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `rebalance.submit_for_review` | operator |
| `approve_rebalance` | `rebalance` | `ApproveRebalance` | `rebalance:{entityId}:approve_rebalance:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `rebalance.approve_rebalance` | approver |
| `schedule_apply` | `rebalance` | `ScheduleRebalanceApply` | `rebalance:{entityId}:schedule_apply:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `rebalance.schedule_apply` | operator |
| `apply_rebalance` | `rebalance` | `ApplyRebalance` | `rebalance:{entityId}:apply_rebalance:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `rebalance.apply_rebalance` | approver; confirm-token required using `rebalance.apply` |
| `rollback_rebalance` | `rebalance` | `RollbackRebalance` | `rebalance:{entityId}:rollback_rebalance:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `rebalance.rollback_rebalance` | approver; confirm-token required using `rebalance.rollback` |
| `cancel` | `rebalance` | `CancelRebalance` | `rebalance:{entityId}:cancel:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `rebalance.cancel` | operator |
| `apply_override` | `rebalance` | `ApplyRebalanceOverride` | `rebalance:{entityId}:apply_override:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `rebalance.apply_override` | approver; confirm-token required using `rebalance.apply_override` |
| `freeze_metric` | `rebalance` | `FreezeRebalanceMetric` | `rebalance:{entityId}:freeze_metric:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `rebalance.freeze_metric` | operator |
| `unfreeze_metric` | `rebalance` | `UnfreezeRebalanceMetric` | `rebalance:{entityId}:unfreeze_metric:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `rebalance.unfreeze_metric` | operator |
| `submit_override` | `rebalance` | `SubmitRebalanceOverride` | `rebalance:{entityId}:submit_override:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `rebalance.submit_override` | operator |
| `advance_step` | `rebalance` | `AdvanceRebalanceStep` | `rebalance:{entityId}:advance_step:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `rebalance.workflow.advance` | operator |
| `rerun_step` | `rebalance` | `RerunRebalanceStep` | `rebalance:{entityId}:rerun_step:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `rebalance.workflow.rerun` | operator |
| `publish_report` | `rebalance` | `PublishRebalanceReport` | `rebalance:{entityId}:publish_report:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `rebalance.publish_report` | operator |

### 8.5 Deployment Actions

Route template: `POST /bff/actions/deployment/{deploymentId}/{actionId}`

| action_id | target_type | command_name | idempotency_key_template | actor_source | trace_propagation | audit_event | policy_check |
|---|---|---|---|---|---|---|---|
| `submit` | `deployment` | `SubmitDeployment` | `deployment:{entityId}:submit:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `deployment.submit` | operator |
| `open_review` | `deployment` | `OpenDeploymentReview` | `deployment:{entityId}:open_review:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `deployment.open_review` | operator |
| `approve` | `deployment` | `ApproveDeployment` | `deployment:{entityId}:approve:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `deployment.approve` | approver |
| `schedule` | `deployment` | `ScheduleDeployment` | `deployment:{entityId}:schedule:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `deployment.schedule` | operator |
| `start_deploy` | `deployment` | `StartDeployment` | `deployment:{entityId}:start_deploy:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `deployment.start_deploy` | approver; confirm-token required |
| `promote_live` | `deployment` | `PromoteDeploymentLive` | `deployment:{entityId}:promote_live:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `deployment.promote_live` | approver; confirm-token required; active caller |
| `rollback` | `deployment` | `RollbackDeployment` | `deployment:{entityId}:rollback:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `deployment.rollback` | approver; confirm-token required |
| `cancel` | `deployment` | `CancelDeployment` | `deployment:{entityId}:cancel:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `deployment.cancel` | operator |
| `promote_stage` | `deployment` | `PromoteDeploymentStage` | `deployment:{entityId}:promote_stage:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `deployment.promote_stage` | approver |
| `reduce_allocation` | `deployment` | `ReduceDeploymentAllocation` | `deployment:{entityId}:reduce_allocation:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `deployment.reduce_allocation` | approver |
| `escalate_diff` | `deployment` | `EscalateDeploymentDiff` | `deployment:{entityId}:escalate_diff:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `deployment.escalate_diff` | approver |

### 8.6 Evolution Program Actions

Route template: `POST /bff/actions/evolution-program/{programId}/{actionId}`

| action_id | target_type | command_name | idempotency_key_template | actor_source | trace_propagation | audit_event | policy_check |
|---|---|---|---|---|---|---|---|
| `submit_evolution_review` | `evolution-program` | `SubmitEvolutionReview` | `evolution-program:{entityId}:submit_evolution_review:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `evolution.submit_evolution_review` | operator |
| `approve_program` | `evolution-program` | `ApproveEvolutionProgram` | `evolution-program:{entityId}:approve_program:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `evolution.approve_program` | approver |
| `stop` | `evolution-program` | `StopEvolutionProgram` | `evolution-program:{entityId}:stop:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `evolution.stop` | operator; active caller |
| `pause_program` | `evolution-program` | `PauseEvolutionProgram` | `evolution-program:{entityId}:pause_program:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `evolution.pause_program` | operator |
| `resume_program` | `evolution-program` | `ResumeEvolutionProgram` | `evolution-program:{entityId}:resume_program:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `evolution.resume_program` | operator |
| `complete_program` | `evolution-program` | `CompleteEvolutionProgram` | `evolution-program:{entityId}:complete_program:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `evolution.complete_program` | operator |
| `retire_program` | `evolution-program` | `RetireEvolutionProgram` | `evolution-program:{entityId}:retire_program:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `evolution.retire_program` | approver |
| `promote_candidate_paper` | `evolution-program` | `PromoteEvolutionCandidatePaper` | `evolution-program:{entityId}:promote_candidate_paper:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `evolution.promote_paper` | approver |
| `promote_candidate_live` | `evolution-program` | `PromoteEvolutionCandidateLive` | `evolution-program:{entityId}:promote_candidate_live:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `evolution.promote_live` | approver; confirm-token required |
| `freeze_generation` | `evolution-program` | `FreezeEvolutionGeneration` | `evolution-program:{entityId}:freeze_generation:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `evolution.freeze_generation` | approver |
| `approve_mutation` | `evolution-program` | `ApproveMutation` | `evolution-program:{entityId}:approve_mutation:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `evolution.approve_mutation` | approver |
| `reject_mutation` | `evolution-program` | `RejectMutation` | `evolution-program:{entityId}:reject_mutation:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `evolution.reject_mutation` | approver |

### 8.7 Research Experiment Actions

Route template: `POST /bff/actions/research-experiment/{experimentId}/{actionId}`

| action_id | target_type | command_name | idempotency_key_template | actor_source | trace_propagation | audit_event | policy_check |
|---|---|---|---|---|---|---|---|
| `run_experiment` | `research-experiment` | `RunResearchExperiment` | `research-experiment:{entityId}:run_experiment:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `research.run_experiment` | operator |
| `attach_to_review` | `research-experiment` | `AttachExperimentToReview` | `research-experiment:{entityId}:attach_to_review:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `research.attach_to_review` | operator |
| `invalidate_result` | `research-experiment` | `InvalidateExperimentResult` | `research-experiment:{entityId}:invalidate_result:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `research.invalidate_result` | approver |
| `promote_artifact` | `research-experiment` | `PromoteResearchArtifact` | `research-experiment:{entityId}:promote_artifact:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `research.promote_artifact` | approver; active caller |
| `retry` | `research-experiment` | `RetryResearchExperiment` | `research-experiment:{entityId}:retry:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `research.retry` | operator |
| `archive` | `research-experiment` | `ArchiveResearchExperiment` | `research-experiment:{entityId}:archive:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `research.archive` | operator |

### 8.8 Artifact Actions

Route template: `POST /bff/actions/artifact/{artifactId}/{actionId}`

| action_id | target_type | command_name | idempotency_key_template | actor_source | trace_propagation | audit_event | policy_check |
|---|---|---|---|---|---|---|---|
| `archive` | `artifact` | `ArchiveArtifact` | `artifact:{entityId}:archive:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `artifact.archive` | operator |

### 8.9 Ranking Formula Actions

Route template: `POST /bff/actions/ranking-formula/{formulaId}/{actionId}`

| action_id | target_type | command_name | idempotency_key_template | actor_source | trace_propagation | audit_event | policy_check |
|---|---|---|---|---|---|---|---|
| `test_formula` | `ranking-formula` | `TestRankingFormula` | `ranking-formula:{entityId}:test_formula:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `rankingformula.test_formula` | operator |
| `submit_formula_review` | `ranking-formula` | `SubmitRankingFormulaReview` | `ranking-formula:{entityId}:submit_formula_review:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `rankingformula.submit_formula_review` | operator |
| `activate` | `ranking-formula` | `ActivateRankingFormula` | `ranking-formula:{entityId}:activate:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `rankingformula.activate` | approver; confirm-token required using `ranking_formula.activate`; active caller |
| `activate_formula` | `ranking-formula` | `ActivateRankingFormula` | `ranking-formula:{entityId}:activate_formula:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `rankingformula.activate_formula` | approver; confirm-token required |
| `deprecate_formula` | `ranking-formula` | `DeprecateRankingFormula` | `ranking-formula:{entityId}:deprecate_formula:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `rankingformula.deprecate_formula` | approver |
| `retire_formula` | `ranking-formula` | `RetireRankingFormula` | `ranking-formula:{entityId}:retire_formula:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `rankingformula.retire_formula` | approver |
| `rollback_formula` | `ranking-formula` | `RollbackRankingFormula` | `ranking-formula:{entityId}:rollback_formula:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `rankingformula.rollback_formula` | approver; confirm-token required |
| `set_active` | `ranking-formula` | `SetActiveRankingFormula` | `ranking-formula:{entityId}:set_active:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `rankingformula.set_active` | approver; confirm-token required |

### 8.10 Runtime Actions

Route template: `POST /bff/actions/runtime/{runtimeId}/{actionId}`

| action_id | target_type | command_name | idempotency_key_template | actor_source | trace_propagation | audit_event | policy_check |
|---|---|---|---|---|---|---|---|
| `restart` | `runtime` | `RestartRuntime` | `runtime:{entityId}:restart:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `runtime.restart` | operator; confirm-token required using `runtime.restart` TTL=180s |
| `stop` | `runtime` | `StopRuntime` | `runtime:{entityId}:stop:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `runtime.stop` | operator or risk_officer; confirm-token required using `runtime.stop` TTL=180s |
| `drain` | `runtime` | `DrainRuntime` | `runtime:{entityId}:drain:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `runtime.drain` | operator; confirm-token required using `runtime.drain` TTL=180s |
| `move` | `runtime` | `MoveRuntime` | `runtime:{entityId}:move:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `runtime.move` | operator |
| `scale` | `runtime` | `ScaleRuntime` | `runtime:{entityId}:scale:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `runtime.scale` | operator |
| `quarantine` | `runtime` | `QuarantineRuntime` | `runtime:{entityId}:quarantine:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `runtime.quarantine` | operator |
| `inspect_logs` | `runtime` | `InspectRuntimeLogs` | `runtime:{entityId}:inspect_logs:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `runtime.inspect_logs` | operator |
| `emergency_kill` | `runtime` | `EmergencyKillRuntime` | `runtime:{entityId}:emergency_kill:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `runtime.emergency_kill` | admin; confirm-token required; no pre-approval |

### 8.11 Tool Actions

Route template: `POST /bff/actions/tool/{toolId}/{actionId}`

| action_id | target_type | command_name | idempotency_key_template | actor_source | trace_propagation | audit_event | policy_check |
|---|---|---|---|---|---|---|---|
| `test_tool` | `tool` | `TestTool` | `tool:{entityId}:test_tool:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `tool.test_tool` | operator |
| `activate_tool` | `tool` | `ActivateTool` | `tool:{entityId}:activate_tool:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `tool.activate_tool` | approver |
| `restrict_tool` | `tool` | `RestrictTool` | `tool:{entityId}:restrict_tool:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `tool.restrict_tool` | approver |
| `unrestrict_tool` | `tool` | `UnrestrictTool` | `tool:{entityId}:unrestrict_tool:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `tool.unrestrict_tool` | approver |
| `deprecate_tool` | `tool` | `DeprecateTool` | `tool:{entityId}:deprecate_tool:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `tool.deprecate_tool` | approver |
| `retire_tool` | `tool` | `RetireTool` | `tool:{entityId}:retire_tool:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `tool.retire_tool` | approver |
| `block_tool` | `tool` | `BlockTool` | `tool:{entityId}:block_tool:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `tool.block_tool` | admin; confirm-token required |
| `disable` | `tool` | `DisableTool` | `tool:{entityId}:disable:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `tool.disable` | approver or risk_officer; confirm-token required using `tool.disable` |

### 8.12 MCP Server Actions

Route template: `POST /bff/actions/mcp-server/{serverId}/{actionId}`

| action_id | target_type | command_name | idempotency_key_template | actor_source | trace_propagation | audit_event | policy_check |
|---|---|---|---|---|---|---|---|
| `connect` | `mcp-server` | `ConnectMcpServer` | `mcp-server:{entityId}:connect:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `mcpserver.connect` | operator |
| `test_connection` | `mcp-server` | `TestMcpServerConnection` | `mcp-server:{entityId}:test_connection:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `mcpserver.test_connection` | operator; active caller |
| `health_check` | `mcp-server` | `CheckMcpServerHealth` | `mcp-server:{entityId}:health_check:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `mcpserver.health_check` | operator; active caller |
| `disable` | `mcp-server` | `DisableMcpServer` | `mcp-server:{entityId}:disable:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `mcpserver.disable` | approver; confirm-token required using `mcp_server.disable` |
| `reenable` | `mcp-server` | `ReenableMcpServer` | `mcp-server:{entityId}:reenable:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `mcpserver.reenable` | approver |
| `retire` | `mcp-server` | `RetireMcpServer` | `mcp-server:{entityId}:retire:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `mcpserver.retire` | admin; confirm-token required |
| `update_env_grants` | `mcp-server` | `UpdateMcpServerEnvironmentGrants` | `mcp-server:{entityId}:update_env_grants:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `mcpserver.update_env_grants` | approver or capability_admin; active caller |
| `rotate_secret` | `mcp-server` | `RotateMcpServerSecret` | `mcp-server:{entityId}:rotate_secret:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `mcpserver.rotate_secret` | admin or capability_admin; confirm-token required using `mcp_server.rotate_secret` |

### 8.13 MCP Tool Actions

Route template: `POST /bff/actions/mcp-tool/{toolId}/{actionId}`

| action_id | target_type | command_name | idempotency_key_template | actor_source | trace_propagation | audit_event | policy_check |
|---|---|---|---|---|---|---|---|
| `grant_persona` | `mcp-tool` | `GrantMcpToolToPersona` | `mcp-tool:{entityId}:grant_persona:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `mcptool.grant_persona` | approver or capability_admin; confirm-token required using `mcp_tool.grant_persona` |
| `grant_env` | `mcp-tool` | `GrantMcpToolEnvironment` | `mcp-tool:{entityId}:grant_env:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `mcptool.grant_env` | approver or capability_admin; confirm-token required; active caller |

### 8.14 Skill Actions

Route template: `POST /bff/actions/skill/{skillId}/{actionId}`

| action_id | target_type | command_name | idempotency_key_template | actor_source | trace_propagation | audit_event | policy_check |
|---|---|---|---|---|---|---|---|
| `deploy_sandbox` | `skill` | `DeploySkillSandbox` | `skill:{entityId}:deploy_sandbox:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `skill.deploy_sandbox` | operator |
| `validate_skill` | `skill` | `ValidateSkill` | `skill:{entityId}:validate_skill:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `skill.validate_skill` | operator |
| `submit_for_approval` | `skill` | `SubmitSkillForApproval` | `skill:{entityId}:submit_for_approval:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `skill.submit_for_approval` | operator |
| `activate_skill` | `skill` | `ActivateSkill` | `skill:{entityId}:activate_skill:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `skill.activate_skill` | approver; confirm-token required |
| `publish` | `skill` | `PublishSkill` | `skill:{entityId}:publish:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `skill.publish` | approver; confirm-token required; active caller |
| `approve` | `skill` | `ApproveSkill` | `skill:{entityId}:approve:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `skill.approve` | approver or capability_admin; confirm-token required using `skill.approve` |
| `deprecate_skill` | `skill` | `DeprecateSkill` | `skill:{entityId}:deprecate_skill:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `skill.deprecate_skill` | approver |
| `deprecate` | `skill` | `DeprecateSkill` | `skill:{entityId}:deprecate:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `skill.deprecate` | approver or capability_admin; confirm-token required using `skill.deprecate` |
| `retire` | `skill` | `RetireSkill` | `skill:{entityId}:retire:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `skill.retire` | approver; confirm-token required; active caller |
| `retire_skill` | `skill` | `RetireSkill` | `skill:{entityId}:retire_skill:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `skill.retire_skill` | approver |
| `block_skill` | `skill` | `BlockSkill` | `skill:{entityId}:block_skill:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `skill.block_skill` | admin; confirm-token required |
| `reopen_skill` | `skill` | `ReopenSkill` | `skill:{entityId}:reopen_skill:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `skill.reopen_skill` | approver |

### 8.15 Channel Actions

Route template: `POST /bff/actions/channel/{channelId}/{actionId}`

| action_id | target_type | command_name | idempotency_key_template | actor_source | trace_propagation | audit_event | policy_check |
|---|---|---|---|---|---|---|---|
| `activate` | `channel` | `ActivateChannel` | `channel:{entityId}:activate:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `channel.activate` | operator |
| `deactivate` | `channel` | `DeactivateChannel` | `channel:{entityId}:deactivate:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `channel.deactivate` | operator |
| `archive` | `channel` | `ArchiveChannel` | `channel:{entityId}:archive:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `channel.archive` | approver |

### 8.16 Generic Approval / Alert / Incident Action Fallbacks

`runAction.ts` now defaults live writes to `/bff/v1/commands`. It still carries an
explicit `KIND_TO_ENTITY_TYPE` map for building command envelopes from the primary
management entity kinds and then falls back to `kind.toLowerCase()` for other
`RunActionInput.kind` values. The deprecated action adapter must not admit arbitrary
fallback kinds; these three fallback route families are documented because they appear
in tests, state-machine catalogs, or current BFF consolidation acceptance language.

| action_id | target_type | command_name | idempotency_key_template | actor_source | trace_propagation | audit_event | policy_check |
|---|---|---|---|---|---|---|---|
| `submit` | `approval` | `SubmitApproval` | `approval:{entityId}:submit:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `approval.submit` | operator |
| `run_validators` | `approval` | `RunApprovalValidators` | `approval:{entityId}:run_validators:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `approval.run_validators` | operator |
| `approve` | `approval` | `ApproveDecision` | `approval:{entityId}:approve:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `approval.approve` | approver |
| `reject` | `approval` | `RejectDecision` | `approval:{entityId}:reject:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `approval.reject` | approver |
| `request_changes` | `approval` | `RequestApprovalRevision` | `approval:{entityId}:request_changes:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `approval.request_changes` | approver |
| `cancel` | `approval` | `CancelApproval` | `approval:{entityId}:cancel:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `approval.cancel` | operator |
| `acknowledge` | `alert` | `AcknowledgeAlert` | `alert:{entityId}:acknowledge:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `alert.acknowledge` | operator |
| `assign` | `alert` | `AssignAlert` | `alert:{entityId}:assign:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `alert.assign` | operator |
| `investigate` | `alert` | `InvestigateAlert` | `alert:{entityId}:investigate:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `alert.investigate` | operator |
| `mitigate` | `alert` | `MitigateAlert` | `alert:{entityId}:mitigate:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `alert.mitigate` | operator |
| `resolve` | `alert` | `ResolveAlert` | `alert:{entityId}:resolve:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `alert.resolve` | operator |
| `close` | `alert` | `CloseAlert` | `alert:{entityId}:close:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `alert.close` | operator |
| `create_incident` | `alert` | `CreateIncidentFromAlert` | `alert:{entityId}:create_incident:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `alert.create_incident` | operator |
| `assign_commander` | `incident` | `AssignIncidentCommander` | `incident:{entityId}:assign_commander:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `incident.assign_commander` | operator |
| `investigate` | `incident` | `InvestigateIncident` | `incident:{entityId}:investigate:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `incident.investigate` | operator |
| `start_mitigation` | `incident` | `StartIncidentMitigation` | `incident:{entityId}:start_mitigation:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `incident.start_mitigation` | operator |
| `mitigation_complete` | `incident` | `CompleteIncidentMitigation` | `incident:{entityId}:mitigation_complete:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `incident.mitigation_complete` | operator |
| `open_postmortem` | `incident` | `OpenIncidentPostmortem` | `incident:{entityId}:open_postmortem:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `incident.open_postmortem` | operator |
| `close_incident` | `incident` | `CloseIncident` | `incident:{entityId}:close_incident:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `incident.close_incident` | approver |

### 8.17 Special-Path Write Endpoints

These endpoints use dedicated paths rather than the generic `/bff/actions/…` template
but must also be adapted to the `/bff/v1/commands` envelope in BFF-CONSOL-019.

| frontend_path | method | command_name | idempotency_key_template | actor_source | trace_propagation | audit_event | policy_check |
|---|---|---|---|---|---|---|---|
| `/bff/approvals/{id}/decide` | POST | `ApproveDecision` / `RejectDecision` / `RequestApprovalRevision` / `EscalateDecision` / `FreezeDecision` | `approval:{id}:decide:{decision}:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `approval.{decision}` | approver |
| `/bff/alerts/{id}/acknowledge` | POST | `AcknowledgeAlert` | `alert:{id}:acknowledge:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `alert.acknowledge` | operator |
| `/bff/v5/interventions/{id}/decide` | POST | `DecideV5Intervention` | `intervention:{id}:decide:{decision}:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `intervention.{decision}` | operator or approver |
| `/bff/confirm-tokens` | POST | `IssueConfirmToken` | `confirm-token:{actionId}:{entityId}:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `{actionId}.confirm_token.issued` | role-specific per high-risk action catalog |
| `/bff/confirm-tokens/{tokenId}/redeem` | POST | `RedeemConfirmToken` | `confirm-token:{tokenId}:redeem:{idemKey}` | `auth_token.sub` | `X-Trace-Id`, `X-Correlation-Id` | `confirm_token.redeem` | operator (token already carries role gate) |

### 8.18 Adapter Design Constraints

The command adapter (BFF-CONSOL-019) must apply these rules uniformly across all entries
in this mapping table:

1. **Actor extraction** — `actor_ref` is always derived from the verified bearer token
   (`auth_token.sub`, `auth_token.roles`). Anonymous or stub sessions are rejected before
   command admission.

2. **Idempotency key forwarding** — The frontend `Idempotency-Key` (or `X-Idempotency-Key`
   alias) is forwarded as-is to the command record. The adapter must not re-mint a new key
   for the downstream command submission; the same key ties the action receipt to the command
   receipt for dual-write reconciliation (BFF-CONSOL-021).

3. **Trace propagation** — `X-Trace-Id` is forwarded or generated if absent. `X-Correlation-Id`
   is forwarded or generated if absent. Both are included in the command envelope `trace` field
   and persisted in the audit record.

4. **Policy decision recording** — The adapter writes a `policy_decision` record (`allow` or
   `deny`) before dispatching to the downstream authority. Denied commands write an audit entry
   and return a non-2xx `BffErrorEnvelope` — they do not fall through to the legacy action path.

5. **High-risk confirm-token gate** — Actions marked "confirm-token required" in §8.1-§8.17
   must reject the command with `CONFIRM_TOKEN_REQUIRED` when no valid confirm token is
   present in the request body or `X-Confirm-Token` header. The token is validated against
   the `action_id` (or the high-risk catalog id named in `policy_check`) and `entity_id`
   before admission.

6. **Audit event name convention** — The `audit_event` column documents the event type string
   written by `pushAudit` in the mock layer. The backend adapter must emit the same event type
   to the audit pipeline so that mock → live audit records are structurally equivalent.

7. **Target typed reference** — The command envelope `target` field uses `{ "type": <target_type>,
   "id": <entityId> }` where `target_type` is the value from the §8.1-§8.17 tables above.

8. **Deprecated action receipt marker** — Responses served from `/bff/actions/*` must include
   the deprecation headers and `deprecated: true` markers described in §3. The persisted
   command foundation context remains `admission_route=POST /bff/v1/commands` with
   `source_route=POST /bff/actions/{entityType}/{entityId}/{actionId}` so audit consumers
   reconcile against the final command receipt, not a separate legacy receipt stream.

---

## 9. Verification

Focused regression evidence:

```bash
python3 -m pytest services/control-plane/bff/tests/test_actions_to_commands_adapter.py services/control-plane/bff/tests/test_command_replay_conflict.py services/control-plane/bff/test_governance_command_submission.py -q
(cd /home/lupin/code/execute-plans && npx vitest run src/lib/bff/__tests__/runAction.test.ts src/lib/bff-v1/__tests__/writes.test.ts)
```

This test set verifies:

- `Idempotency-Key` header is accepted on `/bff/v1/commands` (final route)
- `X-Idempotency-Key` is accepted as a compatibility alias on the final route
- `Idempotency-Key` takes precedence over `X-Idempotency-Key` when both are present
- `idempotencyKey` in the request body is rejected with 400 `INVALID_REQUEST`
- missing idempotency key returns 400 `INVALID_PARAMS` with `precondition_failed=idempotency_key`
- duplicate idempotency key with identical request replays the original `CommandResponse`
- same key with different body returns 409 `IDEMPOTENCY_CONFLICT`
- `/bff/v1/commands` response shape is `CommandResponse<T>` with `status` and `data`
- `/bff/actions/*` remains operational but returns deprecation headers plus
  `deprecated: true` receipt/meta markers
- action adapter audit records still persist `admission_route=POST /bff/v1/commands`
  and the `/bff/actions/*` `source_route`
- frontend `runAction()` live writes default to `/bff/v1/commands`; explicit
  compatibility checks can still opt into the legacy action adapter
- legacy `/api/v1/operator/commands` is unaffected and returns `CommandSubmissionResponse`
- runtime, deployment, approval, and incident command classes persist actor,
  trace, idempotency, policy decision, and audit evidence
- the existing committee command path still uses the shared operator command
  facade

---

*End of BFF Command API Contract (v1)*
