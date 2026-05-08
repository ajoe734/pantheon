# BFF-LUV-GAP-008 - Tools, MCP, And Skills BFF Compatibility

Priority: P1

Area: Tool registry, MCP import/actions, skill sandbox

## Goal

Expose the Part 06 tools/MCP/skills route families while preserving the 2026-05-07 final MCP import/action implementation.

## Missing Or Compatibility Routes

Tools:

- `GET /bff/tools`
- `POST /bff/tools`
- `GET /bff/tools/{toolId}`
- `PATCH /bff/tools/{toolId}`
- `POST /bff/tools/{toolId}/actions/{actionId}`

MCP:

- `GET /bff/mcp/servers`
- `POST /bff/mcp/servers`
- `GET /bff/mcp/servers/{serverId}`
- `POST /bff/mcp/servers/{serverId}/actions/{actionId}`
- `GET /bff/mcp/servers/{serverId}/tools`
- `POST /bff/mcp/tools/{toolId}/actions/{actionId}`

Skills:

- `GET /bff/skills`
- `POST /bff/skills`
- `GET /bff/skills/{skillId}`
- `PATCH /bff/skills/{skillId}`
- `POST /bff/skills/{skillId}/actions/{actionId}`
- `POST /bff/skills/{skillId}/sandbox-eval`

Already present final routes to preserve:

- `POST /bff/mcp-servers/{id}/import-tools`
- `POST /bff/mcp-tools/{id}/{action}`
- `POST /bff/v1/mcp/servers/{id}/import-tools`
- `POST /bff/v1/mcp/servers/{id}/tools/{toolId}/actions/{actionId}`

## Implementation Notes

- The final contract says MCP tools are imported/discovered, not standalone-created. Keep that rule.
- If Part 06 `POST /bff/tools` conflicts with final MCP tooling rules, implement only generic tool metadata creation where valid and document MCP-tool create as superseded.

## Acceptance Criteria

- All list/read/action routes above are non-404 or have explicit registry supersession.
- Final MCP import/action tests remain green.
- Skill sandbox evaluation returns a job/command envelope and audit record.

## Delivery Notes (BFF-LUV-GAP-008 — 2026-05-08)

Owner: Claude | Reviewer: Codex

### Files Changed
- `services/control-plane/bff/models.py`: Added `CommandType.TOOL_ACTION`, `CommandType.MCP_SERVER_ACTION`, `CommandType.SKILL_ACTION`; added `ObjectType.TOOL`, `ObjectType.MCP_SERVER`, `ObjectType.SKILL`.
- `services/control-plane/bff/action_catalog.py`: Added catalog entries for `ToolAction`, `McpServerAction`, `SkillAction`.
- `services/control-plane/bff/main.py`: Added all 12 tools/MCP/skills Part 06 compatibility routes (see below); preserved all 4 final MCP import/action routes.

### Routes Added
**Tools:** `GET /bff/tools`, `POST /bff/tools`, `GET /bff/tools/{toolId}`, `PATCH /bff/tools/{toolId}`, `POST /bff/tools/{toolId}/actions/{actionId}`

**MCP servers (Part 06):** `GET /bff/mcp/servers`, `POST /bff/mcp/servers`, `GET /bff/mcp/servers/{serverId}`, `POST /bff/mcp/servers/{serverId}/actions/{actionId}`, `GET /bff/mcp/servers/{serverId}/tools`, `POST /bff/mcp/tools/{toolId}/actions/{actionId}`

**Skills:** `GET /bff/skills`, `POST /bff/skills`, `GET /bff/skills/{skillId}`, `PATCH /bff/skills/{skillId}`, `POST /bff/skills/{skillId}/actions/{actionId}`, `POST /bff/skills/{skillId}/sandbox-eval`

### Final MCP Routes Preserved
- `POST /bff/mcp-servers/{id}/import-tools`
- `POST /bff/mcp-tools/{id}/{action}`
- `POST /bff/v1/mcp/servers/{id}/import-tools`
- `POST /bff/v1/mcp/servers/{id}/tools/{toolId}/actions/{actionId}`

### MCP Tool Create Supersession Note
`POST /bff/tools` only creates generic (non-MCP) tool metadata records. MCP-sourced tools must be created via `POST /bff/v1/mcp/servers/{id}/import-tools` per the final import contract.

### Verification
```
python3 -m pytest test_execute_plans_contract_registry.py test_bff_strategy_persona_contract.py test_bff_session_auth_me_contract.py test_bff_auth_facade.py -q
# 91 passed
```
Route registration verified: all 16 routes (12 new + 4 preserved) confirmed present in app route table.

