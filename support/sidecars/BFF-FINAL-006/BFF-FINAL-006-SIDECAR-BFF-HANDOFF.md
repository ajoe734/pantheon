# BFF-FINAL-006 · Sidecar: BFF & Frontend Handoff Packet

**Sidecar ID:** BFF-FINAL-006-SIDECAR-BFF-HANDOFF
**Parent task:** BFF-FINAL-006 (Implement MCP server tool import contract)
**Owner:** Codex2 · **Reviewer:** Codex
**Kind:** bff_handoff_packet · **Mutates canonical:** false
**Created:** 2026-05-08

---

## Purpose

This packet supports the current BFF-FINAL-006 owner named in `ai-status.json`
and any frontend consumer preparing the MCP tool import workflow. It identifies
the BFF query gaps, maps the operator journey, and lists frontend integration
expectations for the parent owner to absorb or discard.

This is a support artifact only. It does not modify `models.py`, `main.py`,
permission policy, L1 canonical truth, or runtime/registry/governance code.

---

## 1. Source Snapshot

Inputs read for this sidecar pass:

- `.orchestrator/task-briefs/bff_final_006_sidecar_bff_handoff.md`
- `ai-status.json`
- `docs/bff/execution-tasks/2026-05-07-final/BFF-FINAL-001-contract-foundation.md`
- `docs/bff/execution-tasks/2026-05-07-final/BFF-FINAL-002-idempotency-command-envelope.md`
- `docs/bff/execution-tasks/2026-05-07-final/sidecars/BFF-FINAL-SIDECAR-COPILOT-SPEC-TRACE.md`
- `docs/bff/execution-tasks/2026-05-07-final/sidecars/BFF-FINAL-SIDECAR-GEMINI-SMOKE-MATRIX.md`
- `docs/bff/execution-tasks/2026-05-07-final/sidecars/BFF-FINAL-SIDECAR-GEMINI2-CONFLICT-MAP.md`
- `services/control-plane/permissions/contract.md`
- Current BFF worktree reads of `services/control-plane/bff/main.py` and
  `services/control-plane/bff/models.py`

Creation-time caveat: during the initial sidecar pass, the expected parent
artifact
`docs/bff/execution-tasks/2026-05-07-final/BFF-FINAL-006-mcp-tool-import.md`
was not present and the parent task was still `in_progress`.

Review-time refresh at 2026-05-08T02:31:34Z: the parent artifact now exists,
the MCP import/action routes are present in `services/control-plane/bff/main.py`,
focused coverage exists in `services/control-plane/bff/test_mcp_tool_import.py`,
and `python3 -m pytest services/control-plane/bff/test_mcp_tool_import.py -q`
passes. Treat sections that describe missing parent routes as creation-time
handoff context rather than the current parent implementation state.

---

## 2. BFF State Observed

### Review-time parent state

| Surface | Review-time state |
|---|---|
| Parent artifact | `docs/bff/execution-tasks/2026-05-07-final/BFF-FINAL-006-mcp-tool-import.md` now exists and records delivered implementation scope. |
| Import route | `POST /bff/v1/mcp/servers/{server_id}/import-tools` and frontend alias `POST /bff/mcp-servers/{server_id}/import-tools` are present. |
| Tool action routes | `POST /bff/v1/mcp/servers/{server_id}/tools/{tool_id}/actions/{action}` and frontend alias `POST /bff/mcp-tools/{tool_id}/{action}` are present for `grant`, `revoke`, `disable`, and `test`. |
| Standalone create boundary | Focused tests assert no standalone `POST /bff/v1/mcp/tools`, `POST /bff/v1/tools`, or `POST /bff/mcp-tools` route. |
| Focused verification | `python3 -m pytest services/control-plane/bff/test_mcp_tool_import.py -q` passed at review time. |

### Final contract primitives already available from dependencies

| Dependency | Relevant support for BFF-FINAL-006 |
|---|---|
| BFF-FINAL-001 | `ActionCommandStatus`, required `CommandResponse<T>.data`, `BffErrorEnvelope`, canonical precondition error codes |
| BFF-FINAL-002 | Final route idempotency via `Idempotency-Key`, temporary `X-Idempotency-Key` alias, body `idempotencyKey` rejection, replay/conflict pattern |

### MCP models currently visible in `models.py`

| Model / enum | Use |
|---|---|
| `McpToolClass` | Tool class taxonomy: `research`, `status`, `monitoring`, `execution_signal`, `governance`, `deployment`, `lean_direct` |
| `McpToolActionVerb` | Tool lifecycle action verbs: `grant`, `revoke`, `disable`, `test` |
| `McpToolLifecycleStatus` | Import/action lifecycle status projection |
| `McpToolActionDescriptor` | Imported action descriptor, including `allowStandaloneCreate` and optional `governanceFlag` |
| `McpToolDescriptor` | Imported tool descriptor and schema metadata |
| `McpToolImportRequest` | Import request body for a server descriptor batch |
| `McpToolImportData` | Import result payload with imported and rejected tools |
| `McpToolActionRequest` / `McpToolActionData` | Tool action admission body and result payload |

### MCP routes not found during initial sidecar creation

The creation-time worktree imported the MCP models but no route handlers were
found for:

| Method | Expected path | Status observed |
|---|---|---|
| `POST` | `/bff/mcp-servers/{server_id}/import-tools` | Missing handler |
| `POST` | `/bff/mcp-tools/{tool_id}/grant` | Missing handler |
| `POST` | `/bff/mcp-tools/{tool_id}/revoke` | Missing handler |
| `POST` | `/bff/mcp-tools/{tool_id}/disable` | Missing handler |
| `POST` | `/bff/mcp-tools/{tool_id}/test` | Missing handler |

No focused MCP import test file was found during initial sidecar creation. The
smoke-matrix sidecar already marked BFF-FINAL-006 as pending exact
owner-provided tests. That gap has since been closed by the parent task's
focused MCP import test file.

---

## 3. BFF Query Gaps For BFF-FINAL-006

### 3a. Required write/action handlers

Review-time note: the parent implementation has now landed these write/action
handlers. The table remains useful as frontend handoff context and as a compact
checklist for consumers validating the surface.

| Route | Required behavior |
|---|---|
| `POST /bff/mcp-servers/{server_id}/import-tools` | Validate and import a batch of tool descriptors. Must be idempotent by header key, reject body `idempotencyKey`, return `CommandResponse<McpToolImportData>`, and distinguish imported vs rejected descriptors. |
| `POST /bff/mcp-tools/{tool_id}/grant` | Admit a governed grant action for an imported tool. Must require idempotency header, reason, auth, and permission checks. |
| `POST /bff/mcp-tools/{tool_id}/revoke` | Admit a governed revoke action. Must be idempotent and auditable. |
| `POST /bff/mcp-tools/{tool_id}/disable` | Disable or quarantine an imported tool without deleting history. |
| `POST /bff/mcp-tools/{tool_id}/test` | Run or queue a safe tool test path; must not create a live execution bypass. |

### 3b. Query/read projection gap

BFF-FINAL-006 acceptance focuses on import and actions. The landed parent
artifact designates import/action results as the immediate frontend state
surface; a broader read projection remains a future handoff need if the
frontend must refresh inventory after reconnect or outside the command result
flow.

Minimum useful projection for a future or parent-owned read route:

| Candidate route | Purpose |
|---|---|
| `GET /bff/mcp-servers/{server_id}/tools` | List imported descriptors for a single MCP server |
| `GET /bff/mcp-tools/{tool_id}` | Show tool detail, imported actions, lifecycle status, policy decision, and audit refs |
| `GET /bff/mcp-tools` | Operator-wide tool inventory with server, class, status, risk, and permission summary |

Current interim frontend behavior: use the import/action response for immediate
UI state, then refresh through a later catalog or permission-policy read surface
when that surface is assigned.

### 3c. State ownership gap

Creation-time gap: the parent owner needed to state where imported MCP
descriptors live:

- BFF local dev store only for tests and frontend proof,
- permission policy store,
- control-plane registry,
- MCP adapter service projection,
- or a combination with BFF as read/write facade only.

Review-time note: the parent artifact identifies the delivered contract as a
BFF-local import/action admission surface. This sidecar still does not promote
that implementation detail into canonical durable policy state.

---

## 4. Operator Journey: MCP Tool Import

```text
Operator opens MCP tool intake
   |
   v
Selects MCP server and reviews discovered descriptors
   |
   v
POST /bff/mcp-servers/{server_id}/import-tools
  Idempotency-Key: <uuid>
  body.tools = descriptor batch
   |
   +-- 2xx CommandResponse<McpToolImportData>
   |      Frontend shows importedTools and rejectedTools side by side
   |
   +-- non-2xx BffErrorEnvelope
          Frontend keeps the draft descriptors and shows fixable reason

For each imported tool:
   |
   +-- POST /bff/mcp-tools/{tool_id}/grant
   +-- POST /bff/mcp-tools/{tool_id}/revoke
   +-- POST /bff/mcp-tools/{tool_id}/disable
   +-- POST /bff/mcp-tools/{tool_id}/test
          Idempotency-Key: <uuid>
          { "reason": "...", "scope": {...}, "dryRun": false }
```

Frontend should never expose a generic standalone "create tool" action from
this workflow. Tool descriptors are imported from a governed MCP server flow,
then lifecycle actions are admitted through explicit action routes.

---

## 5. Frontend Integration Spec

### 5a. Import tools

```http
POST /bff/mcp-servers/{server_id}/import-tools
Authorization: Bearer <operator-token>
Idempotency-Key: <uuid>
Content-Type: application/json
```

```jsonc
{
  "serverName": "research-mcp",
  "serverVersion": "2026.05.08",
  "schemaUrl": "https://example.invalid/mcp/schema.json",
  "governance": {
    "executionContext": "research",
    "defaultEffect": "deny"
  },
  "tools": [
    {
      "toolId": "research.vectorbt.backtest",
      "name": "VectorBT Backtest",
      "description": "Run a bounded research backtest",
      "toolClass": "research",
      "inputSchema": {},
      "outputSchema": {},
      "schemaUrl": null,
      "actions": [
        {
          "actionId": "tool.invoke",
          "actionType": "invoke",
          "description": "Invoke the tool",
          "riskLevel": "low",
          "requiresApproval": false,
          "allowStandaloneCreate": false,
          "governanceFlag": null
        }
      ]
    }
  ]
}
```

Expected success envelope shape:

```jsonc
{
  "status": "completed",
  "data": {
    "importId": "mcp-import-<id>",
    "serverId": "research-mcp",
    "importedTools": [
      {
        "toolId": "research.vectorbt.backtest",
        "serverId": "research-mcp",
        "name": "VectorBT Backtest",
        "toolClass": "research",
        "status": "imported",
        "schemaUrl": null,
        "actionCount": 1,
        "standaloneCreateEnabled": false
      }
    ],
    "rejectedTools": [],
    "replayed": false
  },
  "meta": {
    "idempotency": {
      "idempotencyKey": "<uuid>",
      "replayed": false
    }
  }
}
```

The parent owner may choose `accepted` instead of `completed` if the import is
queued through the command store. The frontend should handle all final success
statuses: `accepted`, `queued`, and `completed`.

### 5b. Tool action admission

```http
POST /bff/mcp-tools/{tool_id}/grant
Authorization: Bearer <operator-token>
Idempotency-Key: <uuid>
Content-Type: application/json
```

```jsonc
{
  "reason": "Grant operator console access for paper research workflow.",
  "scope": {
    "channel": "console",
    "executionContext": "research",
    "personaId": "persona-001"
  },
  "dryRun": false
}
```

Expected success envelope shape:

```jsonc
{
  "status": "accepted",
  "data": {
    "toolId": "research.vectorbt.backtest",
    "serverId": "research-mcp",
    "action": "grant",
    "status": "granted",
    "admitted": true,
    "replayed": false
  },
  "meta": {
    "idempotency": {
      "idempotencyKey": "<uuid>",
      "replayed": false
    }
  }
}
```

Use the same body shape for `revoke`, `disable`, and `test`; only the route
verb changes the action.

### 5c. Error envelope handling

The frontend must treat these as non-2xx `BffErrorEnvelope` responses:

| HTTP | Error code | Likely trigger |
|---|---|---|
| 400 | `INVALID_PARAMS` | Missing `Idempotency-Key` / compatibility alias |
| 400 | `INVALID_REQUEST` | Body contains `idempotencyKey`; malformed descriptor; empty import batch |
| 403 | `AUTHZ_DENIED` or existing auth code | Operator lacks role/capability for the requested action |
| 404 | `OBJECT_NOT_FOUND` | Tool or server id not found for action route |
| 409 | `IDEMPOTENCY_CONFLICT` | Same idempotency key with different payload |
| 409 | `APPROVAL_REQUIRED` | Permission engine returns allow-with-approval for a high-risk action |
| 409 | `TWO_MAN_REQUIRED` | Parent decides a tool action requires second signer |

If a specific code is not present in the current enum, the parent owner should
map to the nearest existing final error code and record the mapping in the
parent artifact.

---

## 6. Governance And Permission Notes

The permission contract requires deny-first behavior for tools:

- `lean_direct` tools are denied in `execution_context=live`.
- Governance actions are operator/approver only.
- Deployment and rollback actions are not allowed from chat or public web.
- Some actions may return `allow_with_approval`; BFF should surface that as a
  non-2xx final precondition envelope rather than a successful `requires_*`
  status.

Recommended import-time checks:

1. Reject or quarantine descriptors with `toolClass=lean_direct` and live scope.
2. Do not enable `allowStandaloneCreate` unless a governance flag explicitly
   authorizes it.
3. Treat missing/unknown `governanceFlag` on high-risk descriptors as rejected
   or disabled, not silently granted.
4. Keep `standaloneCreateEnabled=false` in the response unless the above gate
   passes.
5. Audit every grant/revoke/disable/test action with operator identity, reason,
   idempotency key, descriptor version, and policy decision.

---

## 7. SSE / Refresh Handoff

`SSE_CHANNEL_CATALOG` currently includes `mcp` and `tool`; no import-specific
event publication was observed during this pass.

If the parent emits events, recommended event names:

| Channel | Event | Use |
|---|---|---|
| `mcp` | `mcp.tools.imported` | Import result summary and `importId` |
| `mcp` | `mcp.tools.import.replayed` | Replay notice for same idempotency key |
| `tool` | `mcp.tool.granted` | Tool became usable under a scoped policy |
| `tool` | `mcp.tool.revoked` | Grant removed |
| `tool` | `mcp.tool.disabled` | Tool quarantined or disabled |
| `tool` | `mcp.tool.tested` | Safe test completed or queued |

If no SSE events are implemented in BFF-FINAL-006, the frontend should refresh
from the parent-designated read surface after a successful import/action.

---

## 8. Parent Implementation Hints

These are non-binding suggestions for the BFF-FINAL-006 owner:

1. Reuse `_resolve_final_idempotency_key(...)` for all five write routes.
2. Reuse `_reject_body_idempotency_key(...)` before validating import/action
   payloads.
3. Compute a stable request hash from route, path ids, and canonicalized body so
   replay/conflict semantics match BFF-FINAL-002.
4. Return `CommandResponse<McpToolImportData>` and
   `CommandResponse<McpToolActionData>` with required `data`.
5. Keep a route-level test proving `POST /bff/mcp-tools` is absent or returns
   404/405; standalone tool creation must not be silently introduced.
6. Prefer disabled/rejected import results over permissive defaults when
   descriptor risk or governance flags are ambiguous.
7. Add exact focused tests so BFF-FINAL-010 can replace the pending smoke-matrix
   row with a concrete command.

Suggested focused test names:

- `test_bff_final_006_import_tools_returns_command_response`
- `test_bff_final_006_import_tools_rejects_body_idempotency_key`
- `test_bff_final_006_import_tools_replays_same_payload`
- `test_bff_final_006_import_tools_conflicts_on_changed_payload`
- `test_bff_final_006_import_rejects_or_disables_lean_direct_live_tool`
- `test_bff_final_006_standalone_tool_create_route_absent`
- `test_bff_final_006_tool_action_grant_requires_reason_and_idempotency`
- `test_bff_final_006_tool_action_disable_is_idempotent`

---

## 9. Frontend Acceptance Checklist

- [ ] Import screen sends `Idempotency-Key` header, not body `idempotencyKey`.
- [ ] Import response renders `importedTools` and `rejectedTools`.
- [ ] Rejected descriptor rows show `reason` and `preconditionFailed`.
- [ ] Tool lifecycle actions use explicit grant/revoke/disable/test routes.
- [ ] UI does not expose standalone tool creation from the MCP import workflow.
- [ ] UI treats precondition failures as non-2xx `BffErrorEnvelope`, not success
      statuses.
- [ ] UI handles `accepted`, `queued`, and `completed` as final success statuses.
- [ ] UI refreshes from the parent-designated read route or import/action result.
- [ ] `lean_direct` live-scope tools are visually blocked or quarantined when
      returned as rejected/disabled.

---

## 10. Parent Acceptance Evidence Checklist

- [x] Parent artifact for BFF-FINAL-006 exists and names exact implementation
      files.
- [x] `POST /bff/mcp-servers/{server_id}/import-tools` exists and is tested.
- [x] Standalone `POST /bff/mcp-tools` create semantics are absent and tested.
- [x] Header idempotency, replay, and conflict are tested.
- [x] Body `idempotencyKey` rejection is tested.
- [x] Tool actions `grant`, `revoke`, `disable`, and `test` are tested.
- [x] Permission contract guards for operator/admin role, standalone create,
      and `lean_direct` live grant denial are tested.
- [ ] Broader governance/deployment/approval-required mapping remains outside
      this sidecar unless parent owner assigns follow-up coverage.
- [x] Exact focused test command is handed to BFF-FINAL-010 via the parent
      artifact and smoke-matrix row.

---

## 11. Verification Notes For This Sidecar

Commands used during this support pass:

```bash
rg --files | rg 'BFF-FINAL-00[126]|BFF.*006|mcp|tool-import|sidecars/BFF-FINAL'
rg -n "mcp|Mcp|MCP|import_tools|import-tools|tool_id|standalone" services/control-plane/bff/main.py services/control-plane/bff/models.py services/control-plane/bff/action_catalog.py services/control-plane/bff/command_executor.py
rg -n "_resolve_final_idempotency_key|_reject_body_idempotency_key|Idempotency-Key|idempotencyKey|CommandResponse" services/control-plane/bff/main.py services/control-plane/bff/models.py services/control-plane/bff/test_*
git status --short
```

Additional reviewer refresh command:

```bash
python3 -m pytest services/control-plane/bff/test_mcp_tool_import.py -q
```

Result: `6 passed in 9.50s`.

---

*This document is a support artifact. It does not modify canonical truth.*
*The parent owner named in `ai-status.json` decides whether to absorb these
items into the mainline BFF-FINAL-006 implementation.*
