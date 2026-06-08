# Assistant Capability Skill Catalog Ownership

Status: draft-canonical
Last updated: 2026-06-08
Tier: L2 Planning & Execution
Scope: Management AI assistant-skill descriptors, OpenClaw tool/workflow discovery, and BFF projection ownership
Task-ID: ASST-SKILL-001

## Decision Summary

Pantheon assistant skills are descriptors over governed capabilities, not a
second runtime registry. The OpenClaw gateway adapter owns the effective catalog
resolver because it already owns the deny-by-default tool/workflow policy layer.

The existing `GET /api/openclaw-adapter/tools` discovery route remains the
source for per-operator, per-agent, per-mode effective skills. The BFF forwards
authenticated operator context and projects the adapter response; it must not
recompute allowlists or maintain a parallel skill registry.

## Descriptor Schema

Every effective assistant-skill descriptor must include:

| Field | Meaning |
|---|---|
| `id` | Stable descriptor id. Tool descriptors use the OpenClaw tool name. Workflow descriptors use `workflow:<workflow_ref>`. |
| `title` | Human-readable title derived from upstream metadata or the tool/workflow ref. |
| `surface` | Capability surface such as `openclaw_tool`, `openclaw_workflow`, or `assistant_command`. |
| `mode_gate` | Deny-by-default mode gate with explicit `allowed_modes`. |
| `role` | Minimum Pantheon role family required for the descriptor to be effective. |
| `confirm_policy` | Confirmation requirement metadata for UI/command admission. |
| `input_schema` | JSON-schema-like request shape for the handler. |
| `handler_ref` | Adapter-owned handler reference such as `openclaw.tool:search`. |
| `result_surface` | Result projection surface returned by the handler. |

## Resolver Ownership

| Concern | Owner | Rule |
|---|---|---|
| Tool/workflow allowlist | OpenClaw gateway adapter | Empty allowlist means deny all. Always-blocked broker/live/paper/canary/capital/Lean refs are excluded even if configured. |
| Effective skill resolution | OpenClaw gateway adapter | Intersect executable policy allowlist with upstream-reported tools, then apply descriptor mode and role gates. |
| Operator identity | BFF edge | BFF authenticates the operator and forwards `X-Operator-Id` plus role context to the adapter. |
| BFF projection | BFF read store | BFF returns adapter-provided `effective_skills` under OpenClaw ops surfaces without recomputing catalog truth. |
| Invocation admission | Adapter plus downstream command policy | Discovery does not authorize execution by itself. Invocations still pass adapter policy, command policy, idempotency, audit, RBAC, and confirmation gates. |

## Deny-By-Default Rules

- Missing `X-Operator-Id` is rejected before resolution.
- Unknown, unallowlisted, or always-blocked tools/workflows are not effective.
- Unknown modes and user mode have no effective descriptors unless explicitly
  allowlisted by a descriptor mode gate.
- Viewer-only callers can read the surface but receive no operator-only
  effective descriptors.
- Workflow descriptors are repair-mode gated and require confirmation metadata.

## Compatibility

The discovery response keeps the existing `effective_tools` field for current
callers. ASST-SKILL-001 adds `schema_version`, `mode`, `operator_role`,
`effective_workflows`, `effective_skills`, and `skill_resolution`.

No new registry, gateway, or BFF command route is introduced.
