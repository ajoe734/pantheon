# P1-SEARCH-001 Acceptance Note

Owner: Codex2
Reviewer: Codex
Task: OpenClaw governed SearchGateway integration

## Scope Delivered

- OpenClaw search now has a Pantheon adapter endpoint at
  `/api/openclaw-adapter/search/query`.
- The endpoint uses `OpenClawSearchGateway`, which delegates to the governed
  `SearchGateway` instead of invoking upstream OpenClaw tools.
- OpenClaw-facing results are sanitized to evidence bundle ids and citation
  packs only. `answer_context`, `matched_items`, and raw payloads are not
  exposed.
- `SearchGateway` now applies an `available_time` pre-ranking check alongside
  ACL, license, workspace, persona, source type, and environment filters.
- OpenClaw capability metadata exposes governed search without enabling broker,
  runtime, capital, paper, canary, or live execution paths.

## Verification

```bash
pytest services/search/tests -q
pytest services/search/tests/test_governed_search.py services/openclaw-gateway-adapter/test_main.py services/openclaw-gateway-adapter/test_tool_workflow_bridge.py -q
git diff --check -- services/search/gateway.py integrations/openclaw/search_gateway.py services/openclaw-gateway-adapter/main.py services/search/tests/test_governed_search.py services/openclaw-gateway-adapter/test_main.py
```

Results:

- `46 passed` for `services/search/tests`
- `103 passed` for the focused OpenClaw/search/tool bridge set
- `git diff --check` passed

## Boundary Notes

- The OpenClaw search route requires `X-Operator-Id` plus `persona_id` and
  `workspace_id`.
- The OpenClaw result shape intentionally omits answer text and matched item
  internals; downstream callers receive citation-pack references and can fetch
  governed evidence through the evidence plane when authorized.
- Existing tool and workflow policy remains deny-by-default, and the existing
  always-blocked broker/live/paper/capital/LEAN prefixes remain covered by
  tests.
