# EVOCHAIN-007 — Evolution Journal Read API and Filtering

Status: implemented
Owner: Antigravity
Reviewer: Codex
Branch: `task/EVOCHAIN-007`
Merge target: `dev`

## Implemented Contract

This task implements server-side filtering, lineage mapping, and provenance-based origin labeling for the `/bff/management/evolution-journal` endpoint.

### 1. Exact-ID Type-Specific Filters
- **`decision`**: Filters entries where `source_id` matches the input value *and* `entry_type` is exactly `"evolution_decision"`.
- **`mutation_review`**: Filters entries where `source_id` matches the input value *and* `entry_type` is exactly `"mutation_review"`.
- This ensures that filtering by either parameter does not double-return both types of entries when they share the same base ID.

### 2. Persona Lineage Mapping
Instead of free-text substring matching (which caused collisions like `persona-1` matching `persona-10` or summary decoy hits), the endpoint resolves the explicit lineage of a persona:
1. Gathers the persona's associated identifiers from default market default records (`list_personas`).
2. Iteratively computes the transitive closure (runtimes, bindings, plan IDs, and incidents) by checking associations in `list_runtime_bindings` and `list_incidents`.
   - **Shared-Artifact Boundaries**: The transitive closure does NOT use shared `artifact_id` for traversal to avoid crossing shared-artifact persona boundaries.
3. Verifies that the entry's identifiers (such as `target_id`, `artifact_id`, `incident_id`, `runtime_id`, `persona_id`, etc.) have an exact case-insensitive match inside the resolved lineage set.
   - **Collision Prevention**: The row's `entry_type` or `entryType` is excluded from checked identifier sets to prevent parameter collisions (e.g. query `persona=mutation_review` matching all mutation review rows).
   - **Error Handling**: Failures in the dependency lookups (such as read store database failures) are propagated as errors (HTTP 500) rather than being silently swallowed and returned as empty truth.

### 3. Provenance and Honest Origin Markers
The `origin` field projects registered-seed provenance honestly:
1. Check the record's (and nested object's) `metadata` or `provenance` for an explicit `"origin"` field (accepting `"seed"`, `"live"`, or `"unknown"`).
2. Fallback to registered seed ID/marker substring checking (`"seed"`, `"vslice"`, `"87c655c3e3c9"`, etc.).
3. Default all other entries without explicit provenance or seed matches to `"unknown"` instead of silently defaulting them to `"live"`.

## Changed Scope

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/tests/test_bff_b3_evolution_journal.py`
- `docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/EVOCHAIN-007-journal-read-api.md`

## Verification

Executed test suites:
- `services/control-plane/bff/tests/test_bff_b3_evolution_journal.py`
- `services/control-plane/bff/tests/test_bff_b3_persona_fleet.py`

### Test Output
All 14 tests in the focused suites pass cleanly:
```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q \
  services/control-plane/bff/tests/test_bff_b3_evolution_journal.py \
  services/control-plane/bff/tests/test_bff_b3_persona_fleet.py
```
Output: `14 passed, 4 warnings in 22.0s`.

## Residual Risks / Follow-up
- Expiry: Re-verify downstream frontend integration in `EVOCHAIN-009` once those cards are fully wired with server-side page/token logic.
