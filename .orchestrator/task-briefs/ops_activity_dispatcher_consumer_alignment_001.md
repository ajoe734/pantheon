# OPS-ACTIVITY-DISPATCHER-CONSUMER-ALIGNMENT-001

## Objective

Remove the remediation dispatcher's private disjoint activity index so a
legitimate legacy overlap cannot make planning/task generation fail while the
shared logical reader succeeds.

Owner: `Codex`. Reviewer: `Codex2`. Target: `pantheon/dev`. Auto-merge: off.
Depends on: `OPS-ACTIVITY-ROTATION-OVERLAP-PREVENTION-001` merged.

## Scope

- `scripts/dispatch_loop_product_level_remediation_2026-07-13.py`
- its direct tests and redacted evidence only

## Requirements

1. Use the shared logical activity contract for event-ID/payload identity.
   Do not copy legacy fold, pinned exception, or content-lineage rules.
2. Preserve fail-closed behavior for same ID/different payload, same-source
   duplicates, content-addressed overlap/tamper, corrupt JSON/gzip, symlink,
   and source mutation.
3. Preserve deterministic dispatcher output and existing task-generation
   semantics.
4. Use a synthetic multi-source fold fixture to prove legitimate legacy folds
   no longer fail and payload mismatch is still rejected.
5. Run the current 422-source history once as an optional read-only integration
   check. Core acceptance must use isolated roots and must not depend on the
   central history being present.

## Delivery

Run dispatcher tests plus shared reader/status regression suites, py_compile,
and range diff-check. Commit with `LLM-Agent: Codex`, this task ID, and
`Reviewer: Codex2`. Independent exact-head approval is required; owner does
not merge.
