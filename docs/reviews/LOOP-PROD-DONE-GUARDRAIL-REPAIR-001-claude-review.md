# Review: LOOP-PROD-DONE-GUARDRAIL-REPAIR-001

Reviewer: Claude
Date: 2026-07-16
Task: Repair the loop closeout guard so a product task cannot become `done`
while its evidence is blocked, pending, review-required, missing a formal
reviewer verdict, or still has a blocking residual risk; read-only replay
audit over the frozen 18-ID LOOP-PROD product-closure set.

## Verdict: Approved

PR under review: `ajoe734/pantheon#3748` (`task/LOOP-PROD-DONE-GUARDRAIL-REPAIR-001`
at head `b60a8d844481ea08233cc4fc6486c84ef57c93dc9`, base `dev`).

## What Changed On Top Of The Interim Work

PR #3739/#3741 (merged without independent review, per the task brief) had
already landed the core guard: `scripts/loop_done_guardrail.py`'s
`check_task`/`audit_archive_root`, the fixed `FROZEN_ARCHIVE_REPLAY_TASK_IDS`
tuple, and the `validate_loop_completion_claim` gate wired into
`command_done` in `scripts/ai_status.py` (called at
`scripts/ai_status.py:4401`, strictly before `task["status"] = "done"` is
set at line 4405 — satisfies "check before the transition, not only after
status is already done").

This PR (#3748) is the corrected follow-up and fixes three concrete gaps in
that interim version:

1. `audit_archive_root` previously enumerated frozen snapshots with
   `archive_root.glob("LOOP-PROD*.json")` and derived `task_id` with a
   `task.get("id") or snapshot_data.get("task_id")` fallback. Both violated
   "do not derive this set from a glob" and blurred the distinction between
   the frozen ID, the on-disk filename, and the archive's own two separate
   ID fields. Fixed: the frozen set now drives direct per-ID path lookups
   (`frozen_snapshot_paths_by_id`), and `_task_from_archive_snapshot` uses
   only `task.get("id")` for the nested object id.
2. No duplicate-ID detection on the frozen tuple itself. Fixed: added
   `duplicate_frozen_task_ids` detection with a dedicated
   `source_set_errors` entry, and a regression test
   (`test_archive_replay_rejects_duplicate_frozen_task_ids`).
3. The archive snapshot's top-level `task_id` field (independent from the
   nested `task.id`) was never checked against the filename, so a snapshot
   could carry a corrupted/mismatched top-level ID without being flagged.
   Fixed: new precheck compares `snapshot_data["task_id"]` against the
   filename stem and flags both a missing top-level ID and a
   filename/top-level mismatch, plus duplicate-top-level-ID detection
   across the frozen set (`test_archive_replay_rejects_top_level_task_id_mismatch`).

## Independent Verification

- `python3 -m pytest scripts/test_loop_done_guardrail.py -q` — 59 passed.
- `python3 -m py_compile scripts/loop_done_guardrail.py scripts/ai_status.py
  scripts/test_loop_done_guardrail.py` — clean.
- Re-ran the read-only replay independently against the live archive root
  (`/home/lupin/code/pantheon/ai-task-archive/tasks`) with the exact 18-ID
  frozen set:
  `python3 scripts/loop_done_guardrail.py --archive-root
  /home/lupin/code/pantheon/ai-task-archive/tasks --audit-json
  /tmp/verify-audit.json --audit-md /tmp/verify-audit.md`
  → exit 1, 2/18 passed (`LOOP-PROD-REC-001`, `LOOP-PROD-TEACH-001` as
  `valid_closure`), the remaining 16 split across `false_closure` and
  `stale_evidence` with named `<ID>-FALSE-CLOSEOUT-REPAIR` /
  `<ID>-STALE-EVIDENCE-REPAIR` follow-up IDs and exact gap text per task.
  This byte-for-byte matches the committed
  `docs/deployment/evidence/loop-product-level/closeout-truth-audit-2026-07-16.json`
  once `generated_at` is excluded from the comparison.
- Confirmed the archive root itself is unmodified by the replay: no tracked
  file under `ai-task-archive/tasks/` changed (`git status --short` /
  `git diff --stat` empty for that path both before and after running the
  audit).
- Confirmed the top-level `task_id` field written by
  `archive_terminal_task_from_state` (`scripts/ai_status.py:982`) always
  equals `task["id"]` and the archive filename for genuinely-produced
  snapshots, so the new mismatch/missing checks only fire on tampered or
  malformed sources, not on normal `done` archiving — verified against a
  live snapshot (`LOOP-PROD-REC-001.json`).
- Confirmed no file this PR touches (`scripts/loop_done_guardrail.py`,
  `scripts/ai_status.py`, `scripts/test_loop_done_guardrail.py`, the audit
  JSON/MD) has changed on `origin/dev` since this branch's merge-base
  (`17c24edb5`), so the PR's `BEHIND` mergeability state is pure
  fast-forward drift with no real conflict risk in the reviewed surface.
- CI (`Branch CI Gate`: Commit trailers / Runtime mirror guard / Smoke
  acceptance) is green on both required check sets for PR #3748.

## Acceptance Check

| Criterion | Status |
|---|---|
| Reject done when overall_admission is blocked/pending/review_required/rejected/failed | ✅ (pre-existing gate, unchanged by this PR, re-verified) |
| Reject done on any non-`pass`/`not_applicable` acceptance item or blocking residual risk | ✅ (pre-existing gate, re-verified against live archive) |
| Require an approved reviewer verdict before the review_approved→done transition, not only after | ✅ `validate_loop_completion_claim` runs before `task["status"] = "done"` |
| Frozen 18-ID set, not derived from a glob | ✅ fixed in this PR |
| Reject missing/extra/duplicate frozen IDs, malformed snapshot, filename/task-ID mismatch (both nested and top-level) | ✅ fixed/extended in this PR |
| Hash every source before/after and prove no mutation | ✅ `snapshot_sha256_before`/`_after`/`snapshot_hash_unchanged`, independently reproduced |
| Named regressions for the above plus a fully valid manifest | ✅ 9 archive-replay-specific tests, all pass |
| Read-only 18-task replay with machine-readable classification, no archive edits | ✅ reproduced independently, archive untouched |
| Unique repair task ID + exact missing/contradictory proof per non-valid result | ✅ `required_follow_up_task_id` + `gaps` per result |
| PR to dev with required trailers, tests, independent review, merged evidence | ✅ trailers present on all 4 commits; independent review is this document |

## Notes

No code changes required from this review. The PR is mechanically `BEHIND`
`dev` (pure fast-forward drift, no overlapping files) — the owner (Codex2)
should resync before merge, but that does not affect this review's verdict.
Handing back to Codex2 for closeout per
`.orchestrator/skills/task-closeout-finalization.md`.

## Round 2 — Fresh Approval At Current Head (2026-07-16)

Per the task brief's "Final merge gate": the branch advanced past the
originally-approved head through additional `dev` merges (unrelated
`OPS-DEPLOY-WORKFLOW-GUARD-001`, `OPS-DEPLOY-STRICT-POSTURE-QUOTE-001`,
`pint-persona-eligibility-canonical`, `LOOP-PROD-SEQ-RECONCILE-001`,
`OPS-LEASE-READ-AFTER-WRITE-001`, `OPS-LEASE-READ-AFTER-WRITE-PIN-001` PRs)
plus two owner closeout-record commits (`bf589e129`, `1ad72f05e`) that only
touch this task's own brief/review docs. That makes the previous approval
(naming `b60a8d84481ea08233cc4fc6486c84ef57c93dc9`) stale under the gate's
own rule, so this is a fresh independent re-verification naming the new
head.

### Verdict: Approved

PR under review: `ajoe734/pantheon#3748` at head
`1ad72f05e669ce1fa2f6f0ffed35a89b5194f04c` (base `dev`, `mergeStateStatus:
BEHIND`, `mergeable: MERGEABLE`, required checks — Commit trailers /
Runtime mirror guard / Smoke acceptance — all pass).

### Independent Re-Verification

- `git diff --stat b60a8d84481ea08233cc4fc6486c84ef57c93dc9 HEAD --
  scripts/loop_done_guardrail.py scripts/ai_status.py
  scripts/test_loop_done_guardrail.py
  docs/deployment/evidence/loop-product-level/closeout-truth-audit-2026-07-16.json
  docs/deployment/evidence/loop-product-level/closeout-truth-audit-2026-07-16.md`
  — empty. None of the reviewed guard/audit files changed since the
  originally-approved head; every diff since then is unrelated `dev` drift
  or this task's own doc-only closeout commits.
- `python3 -m pytest scripts/test_loop_done_guardrail.py -q` — 59 passed,
  re-run at the current head.
- `python3 -m py_compile scripts/loop_done_guardrail.py scripts/ai_status.py
  scripts/test_loop_done_guardrail.py` — clean.
- Re-ran the read-only replay independently against the live archive root
  (`/home/lupin/code/pantheon/ai-task-archive/tasks`) with the exact 18-ID
  frozen set at the current head: exit 1, 2/18 passed (`LOOP-PROD-REC-001`,
  `LOOP-PROD-TEACH-001`), identical classifications/gaps/repair-task-IDs
  to the Round 1 run. Diffed the fresh `/tmp/verify-audit2.json` against
  the committed `closeout-truth-audit-2026-07-16.json` with `generated_at`
  excluded — byte-for-byte match, and every result's
  `snapshot_hash_unchanged` is `true`.
- `git status --short` under `ai-task-archive/tasks/` shows no modified
  (`M`) frozen snapshot files after the replay — archive still untouched.

### Outage Note

`scripts/ai_status.py` / `scripts/ai-status.sh` are down fleet-wide (every
subcommand, including read-only `show`, raises `RuntimeError: activity
event_id duplicate across sources:
worker-commit-deb673789747a71068bff9f2578ad9f41d7b8253` from
`recover_status_activity_outbox()`). This approval is recorded here, in the
canonical review-artifact channel already established for this task,
because the central status wrapper's `approve`/`note` commands cannot run
during the outage. Once the outage is fixed, this approval should be
mirrored into `ai-status.json` for the task's status history.

No code changes required. Handing back to Codex2: resync the `BEHIND` PR
against current `dev` if GitHub still requires it for merge (should be a
clean fast-forward with no overlapping files per the diff above), then
merge and finalize per this fresh approval.
