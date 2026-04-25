# AUTO-IMPL-TW04-001 Codex Review

Date: `2026-04-20`
Task: `AUTO-IMPL-TW04-001`
Reviewer: `Codex`
Disposition: `review_approved`

## Final Verification

- Re-ran `pytest -q services/control-plane/bff/test_tw04_teaching_replay_contract.py` and confirmed `32 passed`.
- Commit/discard now validate `expected_candidate_snapshot_at` against the latest replayable `preview_trigger.eval_ref.candidate_snapshot_at`, not `artifacts.candidate_artifact_ref`, via [services/control-plane/bff/main.py](/home/edna/code/pantheon/services/control-plane/bff/main.py:5100) and [services/control-plane/bff/main.py](/home/edna/code/pantheon/services/control-plane/bff/main.py:5166), using [services/control-plane/bff/main.py](/home/edna/code/pantheon/services/control-plane/bff/main.py:3454).
- Replay list/detail now share the same degraded and stale surface truth through [services/control-plane/bff/read_store.py](/home/edna/code/pantheon/services/control-plane/bff/read_store.py:6755), [services/control-plane/bff/read_store.py](/home/edna/code/pantheon/services/control-plane/bff/read_store.py:7000), and [services/control-plane/bff/read_store.py](/home/edna/code/pantheon/services/control-plane/bff/read_store.py:7042).
- List rows now expose `allowedActions.canReplay` and keep commit/discard CTA suppression aligned with the published TW-04 contract in [services/control-plane/bff/read_store.py](/home/edna/code/pantheon/services/control-plane/bff/read_store.py:6853).

## Findings

None.

## Reviewer Note

The previously reported gaps are closed. TW-04 replay list/detail/commit/discard behavior now matches the published BFF contract and screen-state semantics closely enough to move the task to `review_approved` and return it to the owner for finalization.
