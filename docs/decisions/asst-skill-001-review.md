# Review: ASST-SKILL-001 — Assistant-Skill Descriptor Schema and Effective-Catalog Resolver

Reviewer: Claude
Review date: 2026-06-08
Status: approved

## Acceptance Criteria Verdict

| Criterion | Verdict | Evidence |
|---|---|---|
| Descriptor schema covers all 9 required fields | PASS | `AssistantSkillDescriptor` frozen dataclass in `tool_workflow_bridge.py:100–125` has `id`, `title`, `surface`, `mode_gate`, `role`, `confirm_policy`, `input_schema`, `handler_ref`, `result_surface`. `to_dict()` serializes all fields. |
| Effective-skill resolution is deny-by-default, reuses existing policy layer | PASS | `_descriptor_effective()` (line 413–421) gates by mode allowlist and role hierarchy. `ToolPolicy` deny-all default unchanged. No new registry created. |
| GET /api/openclaw-adapter/tools returns effective descriptors per operator/agent/mode | PASS | `main.py:1169–1198` wires the endpoint. Accepts `mode` and `operator_role` params (query + header). Response includes `effective_skills`, `schema_version`, `skill_resolution`. |
| Unknown or disallowed skills fail closed | PASS | User mode → empty set. Viewer role → empty set. Always-blocked tools excluded even when in allowlist. Empty allowlist → `deny_all`. |
| No second registry or gateway introduced | PASS | BFF contract §6.1 documents forward-and-project pattern. `openclaw_ops_client.list_effective_tools()` proxies the adapter endpoint only. |
| Tests cover allow, deny, and per-mode differentiation | PASS | `test_effective_skills_include_descriptor_schema_for_allowed_tool`, `test_effective_skills_deny_user_mode`, `test_effective_skills_deny_viewer_role`, `test_workflow_skill_descriptors_are_repair_mode_gated`. BFF surface test `test_openclaw_ops_surface_projects_effective_skill_descriptors` validates end-to-end projection. |

## Observations

- `_ROLE_GATE` enforces a proper role hierarchy; viewer cannot access operator descriptors.
- Frozen dataclass for `AssistantSkillDescriptor` ensures descriptors are not mutated after construction.
- Audit entries are recorded for every invocation attempt regardless of policy outcome.
- `X-Operator-Role` header forwarding in `openclaw_ops_client.py` is correctly implemented.
- Workflow descriptors are repair-mode gated and require `confirm_policy.required=true` as specified.
- BFF contract §6.1 correctly documents the schema — no prose/code divergence found.

## Decision

All acceptance criteria met. No blocking issues. Approve for owner closeout.
