# EVOCHAIN-007 Formal Review

Reviewer: Claude
Owner: Antigravity
Reviewed at: 2026-07-13
Disposition: approve on merits — human must run the status-tool `approve` (self-approval-blocked, see below)

## Scope reviewed

PR #3574 `task/EVOCHAIN-007` -> `dev`: refines the round-1 server-side
filtering (`ce2f84306`, merged as PR #3530) for
`GET /bff/management/evolution-journal`.

Changed files:

- `services/control-plane/bff/main.py`
  - `_evolution_entry_text`: folds `record` identifier fields
    (`artifact_id`, `persona_id`, `target_id`, `runtime_id`,
    `runtime_binding_id`, `persona_capital_binding_id`, `incident_id`,
    `incident_ref`) into the searchable text blob.
  - `persona` filter: lowercases up front; adds an exact-match fallback
    over the same record fields.
  - `mutation_review` / `decision` filters: exact match on `source_id`
    restricted to `entry_type in ("evolution_decision", "mutation_review")`.
- `services/control-plane/bff/tests/test_bff_b3_evolution_journal.py`:
  adds empty-result cases for unmatched `persona` and `decision`.

## Findings

1. **Minor / non-blocking — dead fallback branch.** The `persona` filter's
   `any(field == p_clean for field in (...))` exact-match clause never
   changes the result: `_evolution_entry_text` already concatenates the
   same record fields into the substring-searched text, so anything the
   exact-match clause would catch is already caught by the `in` check.
   Harmless, just redundant; not worth blocking on.
2. **By design, not a bug — `mutation_review` and `decision` filters are
   identical.** Both match on `source_id` across either entry type. This
   matches the data model: a single decision can produce both an
   `evolution_decision` entry and a `mutation_review` projection sharing
   the same `source_id`, and the existing test
   (`test_evolution_journal_server_side_filtering_and_origin`, case 3)
   already asserts both entry types are returned for a `mutation_review=`
   query. No change requested.
3. **Blocking (found, now fixed by reviewer) — stale branch, merge
   conflict.** `dev` had advanced past this branch's last sync
   (`2a8e2fe63`) with ~100+ intervening commits; GitHub reported
   `mergeStateStatus: DIRTY` / `mergeable: CONFLICTING`. Merged
   `origin/dev` into `task/EVOCHAIN-007` (commit `d192500fb`), resolving
   a single trivial conflict in `_evolution_entry_text` (our added
   `record_str` block vs. an unrelated blank-line context shift on the
   `dev` side — no semantic conflict). Pushed; PR is now
   `mergeable: MERGEABLE`.

## Verification

- `python3 -m pytest services/control-plane/bff/tests/test_bff_b3_evolution_journal.py -q`
  -> 4 passed (both before and after the merge-conflict resolution).
- PR #3574 CI (`Commit trailers`, `Runtime mirror guard`, `Smoke
  acceptance`, `Orchestrator Sync`) all green post-push.

## Handoff note

`EVOCHAIN-007` is not present in `ai-status.json` (untracked task
instance — dispatched from the static
`docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/`
packet, never registered via `ai-status.sh assign`). `ai-status.py note`
therefore rejects it as an unknown task, and `ai-status.py approve` was
denied by the session's self-approval classifier because this reviewer
also pushed the merge-conflict-resolution commit on the same branch this
session. A human (or a different lane) should merge PR #3574 directly,
or run the `approve` step, to close this out.
