# PER-002: skills/tools/capabilities read API — Evidence

Task-ID: PER-002
Owner: Claude2
Reviewer: Codex
Commit: 0b1fca5f

## Delivered endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/bff/personas/{persona_id}/skills` | Skills accessible to a persona, from capability snapshot |
| GET | `/bff/personas/{persona_id}/tools` | Tools accessible to a persona, from capability snapshot |
| GET | `/bff/personas/{persona_id}/capabilities` | BFF-style capability snapshot surface |

## Implementation notes

- All three endpoints require `read` role (`_require_read_role`) and verify persona exists via `_ensure_persona_exists`.
- Skills and tools are derived from the persona's capability snapshot (`read_store.get_capability_snapshot_for_persona`).
- For each effective skill/tool ID, the endpoint checks `_merged_skill_records()` / `_merged_tool_records()` for a matching registry entry; if none found, a minimal stub `{"skill_id": id, "id": id, "name": id, "status": "active"}` is returned so the caller always receives an enumerable list.
- Capabilities surface returns `effectiveSkills`, `effectiveTools`, `effectiveWorkflows`, `restrictions`, `generatedAt`, `sourceRefs`, `snapshotId` in camelCase BFF style.

## Verification

```
python3 -m pytest services/control-plane/bff/test_per002_bff_persona_skills_tools_capabilities_contract.py -v
18 passed in 19.94s

python3 -m pytest services/control-plane/bff/test_persona_management.py services/control-plane/bff/test_bff_strategy_persona_contract.py -v
17 passed in 20.46s  (no regressions)
```

## Test coverage

- `test_persona_skills_200_envelope` — data/items/page_info envelope shape
- `test_persona_skills_entries_from_capability_snapshot` — 2 effective skills (risk_review, incident_triage)
- `test_persona_skills_each_entry_has_id_and_name` — id and name present on all items
- `test_persona_skills_404_unknown_persona` — 404 for missing persona
- `test_persona_skills_401_no_auth` — 401 without Authorization header
- `test_persona_skills_meta_surface` — meta.snapshot_at present
- _(mirror tests for tools and capabilities)_

## Closeout note

Reviewer: Codex — approved 2026-05-16, no blocking findings.
Review artifact: support/reviews/PER-002-review-codex.md

Final verification at closeout (2026-05-16):
- `python3 -m pytest services/control-plane/bff/test_per002_bff_persona_skills_tools_capabilities_contract.py -q` → 18 passed
- `python3 -m pytest services/control-plane/bff/test_persona_management.py services/control-plane/bff/test_bff_strategy_persona_contract.py -q` → 17 passed
