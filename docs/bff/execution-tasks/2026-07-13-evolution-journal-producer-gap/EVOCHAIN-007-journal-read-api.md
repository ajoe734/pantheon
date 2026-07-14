# EVOCHAIN-007 — Evolution Journal Read API and Filtering

Status: implemented
Owner: Claude
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
1. Gathers the persona's own directly declared identifiers (`runtime_id`, `binding_id`, `persona_capital_binding_id`, `pool_id`, `capital_pool_id`, `plan_id`, `artifact_id`) from `list_personas`.
2. Reads `list_runtime_bindings` and `list_incidents` **once**, then expands the persona/runtime/binding/plan/pool/incident id sets to a **fixed point** (loop until no set grows), rather than a fixed pass count — a fixed-count loop can silently stop short of a long dependency chain depending on record ordering.
   - **Shared-Artifact Boundaries**: `artifact_id` discovered via binding/incident traversal is never added to the matchable set — only the persona's own directly declared `artifact_id` is. This prevents two unrelated personas that happen to share the same underlying artifact (via independent bindings) from crossing into each other's journal rows.
3. Matches an entry only through fields that are genuine *references to* something the persona's lineage resolved to (`artifact_id`, `persona_id`, `target_id`, `runtime_id`, `runtime_binding_id`, `persona_capital_binding_id`, `incident_id`/`incident_ref`/`linked_incident_id`, `capital_pool_id`/`pool_id`, `plan_id`/`deployment_plan_id`). An entry's own self-identity fields (`source_id`, `id`, `decision_id`, `report_id`, ...) are intentionally excluded — matching those against an arbitrary `persona` query string is a false collision (e.g. `persona=evo-dec-001` must not match a journal row purely because that row's own id happens to equal the query string), not a lineage relationship.
   - **Error Handling**: Failures in the dependency lookups (such as read store database failures) are propagated as errors (HTTP 500) rather than being silently swallowed and returned as empty truth.

### 3. Provenance and Honest Origin Markers
The `origin` field projects registered-seed provenance honestly:
1. Check the record's (and nested object's) `metadata`, `provenance`, or top-level `origin` field for an explicit `"origin"` value (accepting `"seed"`, `"live"`, or `"unknown"`). `read_store._project_service_evolution_decision` preserves the raw record's top-level `origin`/`provenance` fields instead of silently dropping them during projection.
2. Fallback to an **exact** registered-seed-identifier match (`"87c655c3e3c9"`, `"rb-001"`, `"fo-001"`, `"btc-drift"`, `"inc-20260410-001"`, `"inc-20260409-002"`, `"pm-20260409-002"`, `"plan-f-042"`, `"artifact-042"`, `"runtime-042"`, `"binding-042"`, or the `"evo-vslice-"` id prefix) — not a broad substring test. A broad `"seed" in value` substring check previously mislabeled unrelated ids such as `live-seedling-1` as seed-derived.
3. Default all other entries without explicit provenance or an exact seed match to `"unknown"` instead of silently defaulting them to `"live"`.

## Changed Scope

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/tests/test_bff_b3_evolution_journal.py`
- `docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/EVOCHAIN-007-journal-read-api.md`

## Verification

Executed test suites:
- `services/control-plane/bff/tests/test_bff_b3_evolution_journal.py`
- `services/control-plane/bff/tests/test_bff_b3_persona_fleet.py`

### Test Output
All 22 tests in the focused suites pass cleanly:
```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q \
  services/control-plane/bff/tests/test_bff_b3_evolution_journal.py \
  services/control-plane/bff/tests/test_bff_b3_persona_fleet.py
```
Output: `22 passed, 8 warnings in ~24s`.

New coverage added this round: exact-vs-substring seed provenance regression, shared-artifact persona boundary, pool/binding namespace collision, pool-only convergence, persona-capital-binding-only convergence, and a 5-edge chain ordered in reverse dependency order to prove fixed-point (not fixed-count) convergence.

`git diff --check` against the merge-base with `dev` is clean for all files touched this round (no trailing-whitespace findings).

## Residual Risks / Follow-up
- Expiry: Re-verify downstream frontend integration in `EVOCHAIN-009` once those cards are fully wired with server-side page/token logic.
