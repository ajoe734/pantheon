# BFF-FINAL-006 - MCP Server Tool Import Contract

Priority: P0

Depends on: BFF-FINAL-001, BFF-FINAL-002

Area: MCP server import, tool descriptor validation, and v1 tool action admission

## Goal

Implement the BFF-local contract for importing MCP server-owned tool/action descriptors and admitting lifecycle actions against imported tools without exposing a standalone tool-create path.

## Contract Inputs

- MCP tools are imported under a server-scoped route, not created as standalone BFF actions.
- Final BFF command response primitives are used for import/action responses.
- Idempotency is header-only via `Idempotency-Key`, with `X-Idempotency-Key` accepted as a compatibility alias.
- Tool permission policy is deny-first; direct LEAN tool grants are denied for live context.

## Implementation Scope

Delivered files:

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/models.py`
- `services/control-plane/bff/test_mcp_tool_import.py`

## Endpoints

### `POST /bff/v1/mcp/servers/{server_id}/import-tools`

Imports MCP server-owned tool descriptors. Requires an operator/admin identity and an idempotency header.

Frontend-facing alias:

- `POST /bff/mcp-servers/{server_id}/import-tools`

Response shape:

- `CommandResponse[McpToolImportData]`
- `data.importedTools[]` for accepted descriptors
- `data.rejectedTools[]` for descriptor-level validation failures
- `data.replayed` on idempotent replay

### `POST /bff/v1/mcp/servers/{server_id}/tools/{tool_id}/actions/{action}`

Admits lifecycle actions for imported tools. Supported actions:

- `grant`
- `revoke`
- `disable`
- `test`

The route requires the tool to have been imported under the same server id before any action can be admitted.

Frontend-facing alias:

- `POST /bff/mcp-tools/{tool_id}/{action}`

The alias resolves the MCP server from `scope.serverId` or from a unique imported registry match. If the tool id is imported under multiple servers, callers must use the server-scoped v1 route.

## Standalone Create Boundary

No `POST /bff/v1/mcp/tools`, `POST /bff/v1/tools`, or `POST /bff/mcp-tools` route is exposed.

Descriptors that request `allowStandaloneCreate` are rejected unless explicit governance flags are present in the import payload. Even then, the BFF does not expose a standalone create endpoint; lifecycle actions remain server/tool scoped.

## Idempotency

- Same idempotency key and same request payload returns the original import/action id with `replayed=true`.
- Same idempotency key and changed payload returns `409 IDEMPOTENCY_CONFLICT`.
- `idempotencyKey` in the body is rejected by final-contract body-key validation.

## Admission Guards

- Import/action write surfaces require `operator` or `admin`.
- Action admission requires a prior server-scoped import.
- `lean_direct` tools cannot be granted with `executionContext=live`; live execution must use governed signal/artifact flow.

## Verification

```bash
python3 -m pytest services/control-plane/bff/test_mcp_tool_import.py -q
```

## Closeout Evidence

Reviewer approval:

- Codex2 approved `BFF-FINAL-006` on 2026-05-08T02:24:04Z after focused MCP import/action verification.

Implementation state:

- `services/control-plane/bff/main.py` already contains the reviewed MCP import-tools and imported-tool action admission implementation in HEAD.
- `services/control-plane/bff/models.py` already contains the reviewed MCP import/action request and response models in HEAD.
- This closeout commit records the task artifact and focused regression coverage for the delivered contract.

Final verification:

```bash
python3 -m pytest services/control-plane/bff/test_mcp_tool_import.py -q
python3 -m pytest services/control-plane/bff/test_final_contract_primitives.py -q
python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/models.py
```
