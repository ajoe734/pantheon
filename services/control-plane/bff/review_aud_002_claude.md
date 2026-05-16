# Review: AUD-002 AuditAction write engine

Reviewer: Claude
Date: 2026-05-16
Status: APPROVED

## Scope

Reviewed commit 008e73f6 - AuditAction write projection for command-store records.

## Verification

- `py_compile` passed on main.py and test_aud_002_audit_action_write_engine.py.
- AUD-002/audit/final-command/replay/actions suite: 35 passed.
- Capital/strategy/evolution/agora suite: 80 passed, 10 pre-existing deprecation warnings (unrelated).
- Governance command submission suite: 21 passed.
- Known unrelated failure in `test_bff_governance_runtime_risk_audit_contract` (incident fixture ordering) is pre-existing and not introduced by this task.

## Implementation assessment

**Core helpers are correct:**
- `_foundation_audit_for_command_record` - builds an `AuditAction` from command metadata; consistent parameter use.
- `_command_audit_action_from_record` - two-level extraction with safe fallback to empty dict; handles both storage layouts.
- `_audit_datetime` - timezone normalization is correct; graceful `None` return on parse failure.
- `_project_command_record_audit_event` - projects command records to audit event shape cleanly; JSON round-trip to strip non-serializable types is the right pattern.
- `_audit_event_matches` - filter logic is correct; none of the comparisons are inverted.
- `_list_governance_audit_events` - `setdefault` correctly preserves fixture entries and only adds command-store events when no fixture entry with the same id exists.

**Integration points:**
- All six action command helpers (`_capital_bff_action_command`, `_strategy_persona_action_command`, `_evol_exp_bff_action_command`, `_tools_mcp_skills_action_command`, `_gov_bff_action_command`, `_sem_command_response`) now attach `audit_action` to `foundation_ctx` and embed `foundation_ctx` in `audit_record`.
- All audit read endpoints (`/bff/audit`, `/bff/audit/events`, `/bff/audit/entities`, strategy/persona audit subresources, review audit, semantic audit list) updated to use `_list_governance_audit_events` instead of direct `read_store` call.
- `trace_id` is now propagated from `audit_action` instead of hardcoded `None` in two helpers - regression-safe improvement.

**No issues found.** The task scope is met, existing test coverage is preserved, and new coverage proves the write/read round-trip.

## Decision

APPROVED - owner may finalize to done.
