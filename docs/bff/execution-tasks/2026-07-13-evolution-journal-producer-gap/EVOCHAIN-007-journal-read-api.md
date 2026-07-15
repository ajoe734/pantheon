# EVOCHAIN-007 — Evolution Journal Read API and Filtering

Status: in review (round 3 fixes applied; awaiting Codex re-review)
Owner: Claude
Reviewer: Codex
Branch: `task/EVOCHAIN-007`
Merge target: `dev`
PR: ajoe734/pantheon#3595 (supersedes the merged #3574; that PR's earlier
self-approval reviewing #3574's now-superseded behavior should be treated as
stale, not evidence for this round)

## Implemented Contract

This task implements server-side filtering, lineage mapping, and provenance-based origin labeling for the `/bff/management/evolution-journal` endpoint.

### 1. Exact-ID Type-Specific Filters
- **`decision`**: Filters entries where `source_id` matches the input value *and* `entry_type` is exactly `"evolution_decision"`.
- **`mutation_review`**: Filters entries where `source_id` matches the input value *and* `entry_type` is exactly `"mutation_review"`.
- This ensures that filtering by either parameter does not double-return both types of entries when they share the same base ID.

### 2. Persona Lineage Mapping
Instead of free-text substring matching (which caused collisions like `persona-1` matching `persona-10` or summary decoy hits), the endpoint resolves the explicit lineage of a persona:
1. Gathers the persona's own directly declared identifiers (`runtime_id`, `binding_id`, `persona_capital_binding_id`, `pool_id`, `capital_pool_id`, `plan_id`, `artifact_id`) from `list_personas`.
2. Reads canonical persona-capital bindings (`read_store.list_bindings(include_market_persona_defaults=True)`) **and** runtime bindings (`read_store.list_runtime_bindings(...)`) once each, plus `list_incidents` once, then expands the persona/runtime/binding/plan/pool/incident id sets to a **fixed point** (loop until no set grows), rather than a fixed pass count — a fixed-count loop can silently stop short of a long dependency chain depending on record ordering. Both binding sources are read because `list_runtime_bindings` only calls `list_bindings` to *enrich* runtime rows that already exist; a canonical persona-capital binding with no matching runtime row at all would otherwise never surface, silently truncating the persona's own lineage.
   - **Directional closure**: `persona_ids` never grows past the requested root persona. A binding is absorbed into the closure either because it is *owned* (its own `persona_id`/`runtime_id`/`binding_id`/`persona_capital_binding_id`/`plan_id` already belongs to the root's resolved chain), or because it shares a capital pool with the root **and does not declare a different persona's ownership**. A binding that shares a pool but is confirmed to belong to a *different* persona contributes nothing to the closure — this prevents two personas that independently reference the same shared capital pool from leaking each other's private decisions into one another's `?persona=` results.
   - **Shared-Artifact Boundaries**: `artifact_id` discovered via binding/incident traversal is never added to the matchable set — only the persona's own directly declared `artifact_id` is. This prevents two unrelated personas that happen to share the same underlying artifact (via independent bindings) from crossing into each other's journal rows.
3. Matches an entry only through **typed** reference-field/target-type namespaces (persona/runtime/binding/plan/pool/artifact/incident), never a single flattened id blob — two entities in different namespaces can otherwise share the same raw string value (e.g. a `runtime_id` equal to an unrelated decision's `artifact` `target_id`) and falsely collide. An entry's own self-identity fields (`source_id`, `id`, `decision_id`, `report_id`, ...) are intentionally excluded — matching those against an arbitrary `persona` query string is a false collision (e.g. `persona=evo-dec-001` must not match a journal row purely because that row's own id happens to equal the query string), not a lineage relationship.
   - **Direct-lineage projection**: `read_store._project_service_evolution_decision` now preserves the canonical `EvolutionDecision.persona_id`/`capital_pool_id` fields instead of dropping them, so a decision directly associated with a persona (not just via binding/incident traversal) is visible to the filter through both the service-store and local-fallback projection paths.
   - **Dependency degradation**: `personas`, `persona_bindings`, `runtime_bindings`, and `incidents` — the datasets the `?persona=` filter depends on — are now reported under `meta.surfaces`. If any is unavailable (adapter down, not merely an uncaught exception), the filtered request fails closed with `503 DEPENDENCY_UNAVAILABLE` instead of silently returning an authoritative-looking `total=0`.

### 3. Provenance and Honest Origin Markers
The `origin` field projects registered-seed provenance honestly:
1. Check the record's (and nested object's) `metadata`, `provenance`, or top-level `origin` field for an explicit `"origin"` value (accepting `"seed"`, `"live"`, or `"unknown"`). `read_store._project_service_evolution_decision` preserves the raw record's top-level `origin`/`provenance` fields instead of silently dropping them during projection.
2. Fallback to an **exact** registered-seed-identifier match — not a broad substring test. A broad `"seed" in value` substring check previously mislabeled unrelated ids such as `live-seedling-1` as seed-derived, and the registry previously missed several documented seed families:
   - Exact ids: `"87c655c3e3c9"`, `"inc-87c655c3e3c9"`, `"rb-001"`, `"fo-001"`, `"btc-drift"`, `"inc-20260410-001"`, `"inc-20260409-002"`, `"pm-20260409-002"`, `"plan-f-042"`, `"artifact-042"`, `"runtime-042"`, `"binding-042"`.
   - Prefix families: `"evo-vslice-"` and `"ev-seed-"` (covers the registered `services/evolution/seed_data.py` seeds `ev-seed-001..005`).
   - The fallback scanner now also inspects `target_id`/`artifact_id`/`runtime_id`/`runtime_binding_id`/`persona_capital_binding_id`/`plan_id`/`deployment_plan_id` (on both the item's resolved `target` and the inner record), not just self/incident identity fields — a decision that references a registered seed artifact/runtime/binding/plan target, or is swept from the documented seed incident `inc-87c655c3e3c9`, is now honestly labeled `seed` instead of `unknown`.
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

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q \
  services/control-plane/bff/tests/test_bff_b3_evolution_journal.py \
  services/control-plane/bff/tests/test_bff_b3_persona_fleet.py
```
Output: `27 passed, 8 warnings`.

New coverage added this round, targeting the round-3 Codex findings directly:
- Canonical persona-capital binding with **no runtime row at all** still resolves (`list_bindings` traversal, not just `list_runtime_bindings`).
- Two personas independently sharing one capital pool: filtering by persona A must not adopt persona B's identity or leak persona B's private decision.
- Typed-namespace collision: a persona's own `runtime_id` and an unrelated decision's `artifact` `target_id` share the identical raw string — must not cross-match.
- `evolution_decision`/`capital_pool_id` and `persona_id` direct-lineage projection fields are preserved through `_project_service_evolution_decision`.
- Filter dependency surfaces (`personas`/`persona_bindings`/`runtime_bindings`/`incidents`) appear in `meta.surfaces`, and a silently-missing dependency (`dataset_source` reports `"missing"` without raising) fails the persona-filtered request closed with `503`.
- Seed registry/scanner: `ev-seed-*` family member honestly labeled `seed`; a similarly-spelled but unregistered `ev-seeding-*` id stays `unknown`; a decision swept from `inc-87c655c3e3c9` and a decision targeting registered seed artifact `artifact-042` are both honestly labeled `seed`.

Also re-ran and updated a pre-existing test (`test_evolution_journal_server_side_filtering_and_origin`) that had asserted the old free-text substring contract (`?persona=<artifact-id-substring>` matching); it now asserts the corrected lineage-based contract using a real persona id resolved through its declared runtime/incident chain.

`git diff --check` against the merge-base with `dev` is clean for all files touched this round (no trailing-whitespace findings).

## Residual Risks / Follow-up
- Expiry: Re-verify downstream frontend integration in `EVOCHAIN-009` once those cards are fully wired with server-side page/token logic.
- `plan_id` is still treated as an "owned-following" edge during binding traversal (unchanged from prior rounds) rather than a pool-like shared resource; no report of cross-persona leakage via a shared deployment plan has been raised, but it has not been independently adversarially tested the way the shared-pool case now has.
