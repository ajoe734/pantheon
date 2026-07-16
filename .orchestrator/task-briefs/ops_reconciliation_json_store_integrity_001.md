# OPS-RECONCILIATION-JSON-STORE-INTEGRITY-001

## Why this corrective exists

PR #3753 merged as `d55a0caf7772ceb15b7914fe74856929f96d0283`
after the assigned reviewer had recorded a do-not-merge finding. Its atomic
replace prevents interleaved file bytes, but it does not lock the complete
read/modify/write transaction and it silently converts malformed input into a
partial or empty map. Both behaviors can lose records.

This is a fleet implementation task. The planner must not implement the store
repair. Start from current `origin/dev`, preserve PR #3753 as incident
evidence, and deliver a new corrective PR to `dev`.

## Required implementation

1. Add a per-map cross-process lock that covers the complete read, validate,
   mutate, durable-write, and replace transaction. Atomic replace by itself is
   not sufficient.
2. Keep reads safe while a writer replaces the file. A successful write must
   leave one valid JSON object and must not lose an unrelated concurrent
   record.
3. Historical concatenated-map recovery is allowed only when the entire
   non-whitespace input parses as one or more JSON objects whose accepted
   values satisfy the store map contract. Define and test duplicate-ID order;
   the later complete document may win.
4. A malformed, truncated, non-UTF-8, or partially recoverable source must fail
   closed with an explicit error. A failed read or put must leave the source
   bytes unchanged and must not rewrite the file to a partial or empty map.
5. Flush and fsync the temporary file before replace, fsync the containing
   directory after replace where supported, and clean temporary files on both
   success and failure.
6. Do not edit or pre-repair the live dev volume in this task.

## Mandatory regressions

- Reproduce on the PR #3753 implementation that two synchronized distinct
  writers can lose one update. This must be deterministic: use a shared
  process barrier or an instrumented copy of the historical store so both
  writers complete the same pre-write read before either replace, then prove
  exactly one of the two distinct records is lost. A stress loop whose result
  depends on OS scheduling is not accepted.
- Prove repeated process-level concurrent distinct writes retain every record
  and leave parseable JSON.
- Prove a fully valid concatenated map is recovered without losing unique
  records and has deterministic duplicate-ID behavior.
- Prove malformed suffix, truncated JSON, invalid UTF-8, and invalid map values
  all fail closed; after a failed put the original SHA-256 and bytes are
  unchanged.
- Separately inject a temporary-file flush/fsync failure before replace and an
  `os.replace` failure. In both cases prove the original bytes and SHA-256 are
  unchanged and every task-created temporary file is removed.
- Run the focused store suite, reconciliation-drift HTTP suite, relevant
  scheduler tests, and `git diff --check`.

## Delivery and review

- Owner: Claude
- Reviewer: Antigravity
- Target: `ajoe734/pantheon` `dev`
- Auto-merge must remain disabled.
- The reviewer must inspect the exact post-compose head and record a governed
  approval before merge.
- This task fixes store integrity only. It does not close
  `OPS-DEPLOY-WORKFLOW-GUARD-001`.
- After this corrective is merged, the deploy task must rerun Pantheon only and
  prove `reconciliation-drift-svc`, `loop-run-projector-scheduler`, and all
  hosted probes succeed while both deploy workflows remain active and no
  cross-run cancellation occurs.

## Current rejected candidate

PR #3758 head `5bb1110eade8759801e2476c047945afc7dbe06e` and its later
dev-compose-only head `78dbbead23c87c5d451104fbee04b4fcd6c66dc4` are not ready
for review. They use an unsynchronized 60-write stress race and only inject
`os.replace` failure. The owner must add both deterministic tests above, push
a new exact head, and then hand that head to Antigravity.
