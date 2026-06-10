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

## ASST-SKILL-002 Pilot

`assistant.sa_sd.generate` is the first catalog-driven assistant command skill.
The OpenClaw gateway adapter owns its descriptor and exposes it only when the
operator, mode, and `OPENCLAW_ALLOWED_TOOLS` policy make it effective.

The descriptor's `handler_ref` is
`bff.route:POST /bff/assistant/dev-docs/generate`. This points at the existing
BFF dev-docs generate handler; the pilot does not add a second handler or alter
SA/SD generation logic.

The Management AI frontend renders the SA/SD action from the effective
descriptor, not from a hard-coded toolbar button. The BFF still only projects
adapter-provided `effective_skills` and does not recompute catalog truth.

## ASST-SKILL-004 Toolbar Capability Migration

The remaining Management AI toolbar capabilities are adapter-owned
`assistant_command` descriptors. They become visible only when the OpenClaw
adapter allowlist includes the specific tool id and the descriptor mode/role
gates pass:

| Skill id | Handler ref | Result surface |
|---|---|---|
| `assistant.openclaw.ask` | `bff.route:POST /bff/management/nl/ask` | `assistant_management_answer` |
| `assistant.control_mode.status` | `bff.route:GET /bff/assistant/control-mode` | `assistant_control_mode_status` |
| `assistant.transcript.resync` | `bff.route:GET /bff/assistant/sessions/{sessionId}/transcript` | `assistant_transcript_resync` |
| `assistant.orchestrator.status` | `bff.route:GET /bff/assistant/orchestrator/status` | `assistant_orchestrator_status` |

These descriptors point at existing BFF routes and handlers. ASST-SKILL-004
does not add a new BFF command router, OpenClaw registry, or frontend
capability allowlist. The frontend may resolve `{sessionId}` route templates
from descriptor-declared input, then dispatches through the descriptor
`handler_ref`; result projection is selected by `result_surface`, not by
hard-coded capability ids.

## ASST-SKILL-005 Provider Reauth

`assistant.provider.reauth` is an adapter-owned `assistant_command` descriptor
for Codex service-user device-flow reauthentication. It is effective only when
the OpenClaw adapter allowlist includes `assistant.provider.reauth`, the
operator role passes the `operator` gate, and the active mode is `kernel_debug`
or `kernel_repair`.

The descriptor's `handler_ref` is
`bff.route:POST /bff/assistant/provider/reauth`. The BFF must require active
control mode before forwarding the request and must not receive, store, or
forward provider credentials. The adapter starts `codex login --device-auth`
with the mounted service-user `CODEX_HOME`, returns only
`verification_uri`/`user_code` device-flow fields, and exposes background
reauth status for readiness re-probe results.

Credential exchange stays between the operator browser, the identity provider,
and the Codex CLI process. Frontend and BFF surfaces may display the device URL
and user code, but they must not handle OAuth tokens, access tokens, refresh
tokens, or mounted credential file contents.
