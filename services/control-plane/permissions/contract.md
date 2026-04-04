# OpenClaw Tool Permission Contract

**Task:** OC-001  
**Owner:** Codex  
**Reviewer:** Claude  
**Status:** DRAFT — aligned with Router v1 evaluator, pending governance review

---

## 1. Purpose

This contract defines how OpenClaw decides which tools a persona, operator, or cron workflow may use.

The goal is not only convenience. It is a hard governance boundary:

- personas cannot reach live execution directly
- channel trust level changes what tools are available
- high-risk actions require explicit approval hooks
- cron workflows are separated from live trading permissions

The machine-readable policy shape lives in:

- `services/control-plane/permissions/tool_policy_schema.json`

---

## 2. Non-Negotiable Rule

The architecture diagram includes a direct path from OpenClaw tools to LEAN.
That path is a governance vulnerability unless constrained.

**Rule:**

- any tool with a direct `LEAN` call surface may run only in `paper` or `backtest` context
- live execution must always flow through:

```text
Registry -> approved artifact or signal snapshot -> SignalStore / artifact loader -> LEAN
```

- no persona, channel tool, or cron job may bypass this path for live trading

This rule must be enforced in both:

- Router dispatch policy
- tool permission evaluation

---

## 3. Subjects

Permissions are evaluated for a subject, not just a tool.

### 3.1 Subject Types

| Subject type | Example | Notes |
|---|---|---|
| persona | research persona, execution persona | default subject for conversational routing |
| operator | human using console or approval flow | may approve high-risk actions |
| cron | ingest / review / retrain / deploy workflow | non-interactive subject with narrow scope |

### 3.2 Context Dimensions

| Dimension | Values |
|---|---|
| `channel` | `telegram`, `discord`, `web`, `console`, `cron` |
| `channel_tier` | `chat`, `web`, `operator`, `system` |
| `role` | `persona`, `operator`, `approver`, `system` |
| `execution_context` | `research`, `status`, `monitor`, `paper`, `live` |

Recommended default mapping:

- `telegram`, `discord` -> `chat`
- `web` -> `web`
- `console` -> `operator`
- `cron` -> `system`

---

## 4. Tool Classes

| Tool class | Examples | Default posture |
|---|---|---|
| `research` | `QlibTool`, `VectorbtTool`, `FinRLTool`, `QuantLibTool` | allow for research-capable personas and cron |
| `status` | `StatusTool` | broad allow |
| `monitoring` | `MonitoringTool` | broad allow, read-only |
| `execution_signal` | `ExecutionTool` -> SignalStore | restricted, promotion-state aware |
| `governance` | `GovernanceTool` | operator / approver only |
| `deployment` | artifact promote / rollback | operator / system only |
| `lean_direct` | any tool calling LEAN runtime directly | deny for live, allow only paper/backtest |

---

## 5. Evaluation Order

Permission evaluation must be deterministic and deny-first.

### 5.1 Order

1. resolve subject context (`persona_id`, `channel`, `role`, `execution_context`)
2. apply global deny rules
3. apply subject-specific deny rules
4. check explicit allowlist rule
5. enforce approval hook if required
6. enforce promotion-state restrictions for execution-capable actions

If no explicit allow rule matches, the result is `deny`.

---

## 6. Required Deny Rules

These rules are mandatory and must not be removed by persona configuration.

1. deny any `lean_direct` tool when `execution_context=live`
2. deny any execution request for artifacts in `draft`, `candidate`, or `retired`
3. deny governance approval actions for non-operator roles
4. deny deployment and rollback actions from `telegram`, `discord`, and public `web` channels
5. deny cron jobs from writing directly to live execution surfaces

---

## 7. Approval Hooks

Some operations may be allowed only after approval.

| Action | Requires approval | Notes |
|---|---|---|
| promote `paper -> live` | yes | operator or delegated approver only |
| rollback live artifact | yes | operator only |
| write execution signal in `live` context | yes | must also pass promotion-state check |
| modify permission policy | yes | governance change |

Approval hook contract:

- permission engine returns `allow_with_approval`
- Router or Governance service converts this to an approval workflow
- execution must not proceed until approval is recorded

---

## 8. Policy Object Shape

Every persona or system workflow is governed by a policy object.

Core fields:

| Field | Required | Description |
|---|---|---|
| `policy_version` | yes | policy schema version |
| `policy_id` | yes | unique policy id |
| `subject_scope` | yes | persona / role / channel scope |
| `default_effect` | yes | must be `deny` |
| `global_deny_rules` | yes | non-removable deny rules |
| `tool_rules` | yes | explicit allow or deny entries per tool |

Tool rule minimum fields:

| Field | Required | Description |
|---|---|---|
| `tool_id` | yes | stable tool identifier |
| `effect` | yes | `allow` or `deny` |
| `tool_class` | yes | class from §4 |
| `intent_allowlist` | no | intent patterns |
| `channel_allowlist` | no | allowed channels |
| `role_allowlist` | no | allowed roles |
| `execution_context_allowlist` | no | `research`, `paper`, `live`, etc. |
| `promotion_state_allowlist` | no | required for execution-capable tools |
| `requires_approval` | no | whether approval gate is mandatory |
| `reason` | no | audit-friendly explanation |

---

## 9. Router Integration Points

`P4-001` now contains a v1 deny-first evaluator.
The final Router integration should:

1. load the policy object for the current subject
2. evaluate the requested intent and tool against the contract above
3. return:
   - `allow`
   - `deny`
   - `allow_with_approval`
4. log the decision to audit storage

Current v1 status:

- Router already enforces a minimal deny-first evaluator
- session TTL and per-channel rate limits are now locked in `P4-001`
- exact policy storage backend is still open

### Required subject resolution for v1

Router must not assume every request has `role=persona`.

Minimum v1 mapping:

- `console` -> `role=operator`
- `cron` -> `role=system`
- `telegram`, `discord`, `web` -> `role=persona`

This is required so governance and deployment actions that are allowed for operators
do not get incorrectly denied by the router.

### Follow-up beyond v1

- load full policy objects from storage
- resolve subject identity from authenticated session instead of channel-only mapping
- log permission decisions to durable audit storage

---

## 10. Example Decisions

| Scenario | Decision | Reason |
|---|---|---|
| Telegram persona asks for Qlib backtest | allow | research tool in chat context |
| Telegram persona asks to push live strategy | deny | no live deployment from chat channel |
| Console operator asks to promote artifact to live | allow_with_approval | high-risk state transition |
| Cron ingest job fetches OpenAlex metadata | allow | system research context |
| Cron job calls LEAN directly in live mode | deny | bypasses REG -> SIG -> L path |

---

## 11. Review Focus

Claude should review this contract for:

- consistency with Router dispatch assumptions in `P4-001`
- whether approval hooks are sufficient for high-risk operations
- whether the deny-first model closes obvious governance bypasses
