# Task Brief: LOOP-PROD-DONE-GUARDRAIL-REPAIR-001

Owner Codex2 implements. Reviewer Claude independently reviews.

Replay the live archive read-only from
`/home/lupin/code/pantheon/ai-task-archive/tasks`. The frozen set is exactly:
LOOP-PROD-AGORA-001, LOOP-PROD-AGORA-002, LOOP-PROD-ALPHA-001,
LOOP-PROD-AUTH-001, LOOP-PROD-CAP-001, LOOP-PROD-CONS-001,
LOOP-PROD-DEP-001, LOOP-PROD-DIST-001, LOOP-PROD-GAP-ADDENDUM-001,
LOOP-PROD-GAP-ADDENDUM-002, LOOP-PROD-IMIT-001, LOOP-PROD-MAI-001,
LOOP-PROD-OODA-001, LOOP-PROD-REC-001, LOOP-PROD-RUNTIME-BOOT-001,
LOOP-PROD-SRC-001, LOOP-PROD-TEACH-001, LOOP-PROD-TEL-001.

Do not derive the set from a glob. Reject missing/extra/duplicate IDs,
malformed snapshots and filename/task-ID mismatch. Hash every source before
and after. Classify each as valid_closure, stale_evidence or false_closure;
every non-valid result needs a unique repair task ID and exact missing proof.

Tests must cover the exact set, missing/extra/duplicate/malformed sources,
ID mismatch, immutable hashes, classifications and repair IDs. PR #3739 and
#3741 are interim and cannot close the task. Submit a corrected follow-up PR
and hand off to Claude.
