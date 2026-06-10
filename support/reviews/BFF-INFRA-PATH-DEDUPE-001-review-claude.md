---
task: BFF-INFRA-PATH-DEDUPE-001
reviewer: Claude
owner: Codex
date: 2026-05-25
status: approved
---

# Review: BFF-INFRA-PATH-DEDUPE-001 — Dedupe 12 snake_case duplicate route families

## Verdict: APPROVED

PR #580 merged at a72b2fba. Code commit 0cc244e9.

## Scope verified

The commit addresses all 12 route families listed in the CANONICAL_PATH_NAMING.md action log:

| # | Family | Action taken |
|---|---|---|
| 1 | personas | `{id}` declaration removed; nested action route returns 410 → `/bff/actions/persona/{persona_id}/{action_id}` |
| 2 | capital-pools | `{id}` declaration removed; nested action route returns 410 → `/bff/actions/capitalPool/{pool_id}/{action_id}` |
| 3 | deployments | `{id}` declaration removed; nested action route returns 410 → `/bff/actions/deployment/{deployment_id}/{action_id}` |
| 4 | rebalances | `{id}` declaration removed; nested action route returns 410 → `/bff/actions/rebalance/{rebalance_id}/{action_id}` |
| 5 | incidents | `{id}` declaration removed; nested action route returns 410 → `/bff/actions/incident/{incident_id}/{action_id}` |
| 6 | runtimes | `{id}` declaration removed; nested action route returns 410 → `/bff/actions/runtime/{runtime_id}/{action_id}` |
| 7 | skills | `{id}` declaration removed; nested action route returns 410 → `/bff/actions/skill/{skill_id}/{action_id}` |
| 8 | tools | `{id}` declaration removed; nested action route returns 410 → `/bff/actions/tool/{tool_id}/{action_id}` |
| 9 | mcp-servers | `/bff/mcp/servers` and `/bff/mcp/servers/{server_id}` return 410 → canonical kebab-case forms |
| 10 | mcp-tools | `/bff/mcp/tools/{tool_id}/actions/{action_id}` returns 410 → `/bff/mcp-tools/{tool_id}/{action_id}` |
| 11 | ranking-formulas | `/bff/ranking/formulas` family (all verbs + action sub-route) returns 410 → canonical `/bff/ranking-formulas` forms |
| 12 | strategy actions | `/bff/strategies/{strategy_id}/actions/{action_id}` returns 410 → `/bff/actions/strategy/{strategy_id}/{action_id}` |

## Implementation quality

- `_deprecated_bff_path_response()` helper emits the correct headers (`X-Deprecated`, `X-Deprecated-At`, `Deprecation`, `Sunset`, `Link`, `Warning`, `X-Pantheon-Replacement-Route`) and a 410 status.
- Canonical routes (`/bff/mcp-servers`, `/bff/mcp-tools`, `/bff/ranking-formulas`, generic `/bff/actions/*`) remain active and unmodified.
- `{deployment_id}` and `{rebalance_id}` snake_case parameter renames are applied to the PATCH handlers; logic correctly updated to pass `deployment_id` / `rebalance_id` to downstream commands.
- `Body(default_factory=dict)` substitution on routes that now short-circuit to 410 prevents body-validation errors before the handler returns.
- Duplicate entries removed from the generic alias batch decorator (`/bff/personas`, `/bff/strategies`, `/bff/mcp-servers/{id}/import-tools`).

## Test coverage

All 4 regression tests in `test_bff_path_dedupe.py` pass:

- `test_deprecated_alternate_url_families_return_410_with_headers` — GET + write variants for mcp/servers and ranking/formulas families
- `test_deprecated_nested_action_families_return_410_with_headers` — all 9 nested action families
- `test_path_parameter_dedupe_keeps_only_snake_case_canonical_templates` — asserts canonical templates present, legacy `{id}` forms absent
- `test_targeted_duplicate_route_registrations_are_removed` — POST /bff/personas, /bff/strategies, /bff/mcp-servers/{}/import-tools each count == 1

Verified locally:
```
python3 -m pytest services/control-plane/bff/tests/test_bff_path_dedupe.py -q
4 passed in 3.47s
```

Pre-existing unrelated failures in `test_bff_b2_list_detail_facade.py` and `test_actions_to_commands_adapter.py` / `test_command_replay_conflict.py` are noted in the commit message and are not caused by this change.

## Docs artifacts

- `CANONICAL_PATH_NAMING.md` — action log appended (Section 2); accurately describes what was removed/deprecated.
- `BFF_API_GAP_delta_v3_spec.md` — PATH-DEDUPE-001 section added with summary and verification command.
- `execute-plans/.lovable/audits/bff-backend-gap-2026-05-25-delta-v4.md` — PATH-DEDUPE-001 audit entry added.

## Boundary compliance

The commit stays within the declared owned layer (BFF route declarations, deprecation responses, tests, naming/audit records). No L1 canonical architecture docs modified. No error envelope shape changed. No deployment workflow or live environment configuration touched.

## Notes for owner finalization

No follow-up items required. Owner may run `done` closeout after confirming the approved scope is still true in the current worktree.
