---
task_id: CONSOLE-DATA-REGISTRY
reviewer: Claude2
owner: Claude
pr: 1689
status: approved
reviewed_at: 2026-06-15
---

# Review: CONSOLE-DATA-REGISTRY — Populate /bff/skills,/tools,/mcp-servers,/mcp-tools

## Decision: APPROVED

All acceptance criteria met. Implementation is clean, complete, and follows established patterns.

## Acceptance Criteria Check

| Criterion | Status |
|---|---|
| /bff/skills count=5 status=ok | ✅ 5 skills in data/skills.json projected from skills.yaml |
| /bff/tools count=4 status=ok | ✅ 4 tools in data/tools.json |
| /bff/mcp-servers count=1 status=ok | ✅ 1 mcp-server in data/mcp_servers.json |
| /bff/mcp-tools count=4 status=ok | ✅ 4 mcp-tools in data/mcp_tools.json |
| Contract tests in services/control-plane/bff/tests | ✅ 16 new tests (100% pass) + 29 pre-existing pass |

## CI Status

PR #1689 — all 3 required branch-CI checks pass:
- Commit trailers: SUCCESS
- Runtime mirror guard: SUCCESS
- Smoke acceptance: SUCCESS

## Implementation Quality

**read_store.py** — 4 new dataset entries added to `ServiceBackedReadAdapter._DATASETS` following the exact established pattern (env var, dirs, filenames, keys, snapshot_key). 4 new `list_*` methods on `ReadSurfaceStore` delegating cleanly to `_read_dataset_records`. No novel patterns introduced.

**main.py** — 4 fixture functions updated with store-first dispatch: try `read_store.list_*()`, fall through to existing fixture pack if empty. Fully backwards-compatible for tests without stores.

**projection script** — Reads from real domain producer (`services/control-plane/skills/skills.yaml`). No data fabrication. Handles missing YAML or unreachable RWG endpoint gracefully with warnings.

**data JSON files** — 5 skills, 4 tools, 1 mcp-server, 4 mcp-tools. All projected from skills.yaml. `source` field set to `control_plane_skills_registry` for provenance.

**docker-compose.yml** — 4 new env vars wired with `/data/bff/*.json` defaults, consistent with existing research store var pattern.

**tests** — 16 new contract tests covering: 200+count>0 for all surfaces, surface status=ok, individual record id/name fields, 401 without auth on all surfaces, and governance `live_execution_allowed=False` stub-dispatch safety invariant.

## Notes

- Test count in commit message (16+29=45) differs slightly from "50" in task brief — not blocking, both show passing coverage.
- `mcp_servers.json` file is missing trailing newline; negligible.
- Governance safety invariant (`paper_only=true`, `live_execution_allowed=false`) is correctly encoded in the MCP server record and asserted in tests.
