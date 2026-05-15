# SVC-OPENCLAW-BFF-OPS-SURFACE — Frontend Change Spec

## Feature

- Feature ID: `SVC-OPENCLAW-BFF-OPS-SURFACE`
- Screen ID: `screen-openclaw-ops-surface`
- Workbench: Operator Console
- Packet status: contract-ready

## Summary

Build an OpenClaw operations view against the Pantheon BFF. The view shows upstream reachability, Pantheon-owned session lifecycle records, tool/workflow policy and invocation audit, explicit degraded reasons, and paper/live gate state. It must not call the OpenClaw adapter directly and must not expose broker, paper, live, or capital-binding activation.

## API Integration

Use the shared BFF client. Do not call `openclaw-gateway-adapter` from the browser.

### Fetch OpenClaw ops

```
GET /api/v1/operator/openclaw/ops
```

Supported query params:

| Param | Default | Bounds | Meaning |
|---|---:|---:|---|
| `session_limit` | `25` | `1..100` | Max lifecycle rows returned |
| `audit_limit` | `20` | `1..100` | Max invocation audit rows returned |
| `state` | absent | backend enum | Optional lifecycle state filter |
| `operator_id` | absent | admin-only cross-operator filter | Optional operator filter |
| `agent_id` | absent | opaque | Optional effective-tool lookup key |
| `session_id` | absent | opaque | Optional effective-tool session key |

Alias:

```
GET /api/v1/operator/openclaw/tool-workflow-bridge
```

### Response shape

The route returns:

- `data.overall_status`: `ok | degraded | unavailable`
- `data.upstream`: reachability and degraded reason
- `data.gate_state.paper_adapter.enabled`: always backend-owned; do not infer
- `data.gate_state.live_adapter.enabled`: always backend-owned; do not infer
- `data.session_lifecycle.sessions[]`: session id, agent id, type, state, operator, last error, audit summary, `allowedActions`
- `data.tool_workflow.policy`: adapter policy snapshot
- `data.tool_workflow.audit.entries[]`: tool/workflow invocation audit, including denied policy decisions
- `data.degradation.reasons[]`: backend-owned degraded reasons
- `meta.surfaces`: surface health for the composed route and adapter sub-surfaces

## Commands

Only these BFF session lifecycle commands are available:

```
POST /api/v1/operator/openclaw/sessions
POST /api/v1/operator/openclaw/sessions/{session_id}/cancel
```

Both require:

- `Authorization: Bearer ...`
- operator or admin role
- `X-Idempotency-Key`

Do not render command controls unless the corresponding backend `allowedActions` field is true. Tool invocation and workflow trigger commands are intentionally not exposed by the BFF in this packet; show their audit state only.

## Constraints

- Do not enable or offer UI controls for paper, live, broker execution, or capital binding.
- Do not infer policy from role labels, gate names, session state, or allowlist text.
- Denied audit rows are healthy fail-closed evidence, not UI failures.
- `overall_status = degraded` keeps the view read-only and visible.
- `overall_status = unavailable` renders the unavailable state with `data.degradation.reasons[]`.
- Do not show an empty success state when adapter or upstream status is unavailable.
