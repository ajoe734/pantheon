# EVOCHAIN-007 Review Notes

- **Task ID**: EVOCHAIN-007
- **Title**: Server-side journal filtering + seed origin marker
- **Owner**: Claude
- **Reviewer**: Antigravity
- **Date**: 2026-07-14

## Review Summary

All acceptance criteria outlined in the task brief have been fully satisfied. The server-side evolution journal filtering, pagination, and seed origin marker implementation is correct, robust, and verified by an extensive suite of new contract tests.

### Checked Points

1. **Exact-ID Type-Specific Filters**:
   - `decision` and `mutation_review` parameters correctly filter entries by `source_id` matching the query parameter and verifying that the `entry_type` is exactly `"evolution_decision"` or `"mutation_review"`. This prevents type cross-matching.

2. **Persona Lineage Mapping**:
   - The `/bff/management/evolution-journal` endpoint successfully resolves explicit persona lineage rather than doing loose substring matching.
   - Persona lineage is resolved through `list_bindings` (including market persona defaults) and `list_runtime_bindings`, plus `list_incidents`, converging to a fixed point.
   - Directional closure is correctly enforced (i.e. `persona_ids` doesn't grow past the requested root).
   - Shared-pool boundary check correctly isolates decisions belonging to other personas that share the same capital pool.
   - Shared-artifact boundary check correctly prevents unrelated personas sharing an artifact from cross-matching.
   - Matching matches only through typed reference-field namespaces to avoid cross-type namespace collisions.
   - Direct-lineage projection preserves `persona_id` and `capital_pool_id`.
   - Filter dependency degradation returns `503 DEPENDENCY_UNAVAILABLE` when critical datasets are unavailable.

3. **Provenance and Honest Origin Markers**:
   - The `origin` field projects `"seed"`, `"live"`, or `"unknown"` honestly.
   - Fallback to exact registered seed identifiers (with prefix matching for `evo-vslice-` and `ev-seed-` families) is implemented.
   - Default fallback is correctly set to `"unknown"`.

## Verification Evidence

Tested locally in `/tmp/pantheon-worker-worktrees/pantheon/evochain-007` workspace.

### 1. Evolution Journal Tests
Command:
```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/tests/test_bff_b3_evolution_journal.py
```
Output:
```text
======================= 18 passed, 4 warnings in 26.92s ========================
```
New test coverage verifies:
- Canonical persona-capital binding with no runtime row resolves correctly.
- Correct isolation when two personas share one capital pool.
- Typed namespace collision prevention.
- Preservation of projection fields.
- 503 dependency failure handling.
- Seed registry exact matching.

### 2. Persona Fleet Tests
Command:
```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/tests/test_bff_b3_persona_fleet.py
```
Output:
```text
======================== 9 passed, 4 warnings in 47.25s ========================
```

## Conclusion

The changes are approved. The task can be handed back to the owner for final closeout.
