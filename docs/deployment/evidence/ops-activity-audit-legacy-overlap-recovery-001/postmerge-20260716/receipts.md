# Deployment Receipts: OPS-ACTIVITY-AUDIT-LEGACY-OVERLAP-RECOVERY-001

## PR Merge Commit Receipt
- **PR #3773 Merge SHA**: `64844eef7e87c63c955c98fa95579992aa3af5e2`
- **Merge Target**: `dev`

## Supervisor installation Receipt
- **Installation Path**: `/home/lupin/pantheon-ci-deploy/dev-root`
- **Git HEAD SHA Before Sync**: `af912820eda9f6ca54f9b4333766ae30a9830120`
- **Git HEAD SHA After Sync**: `64844eef7e87c63c955c98fa95579992aa3af5e2`
- **Supervisor PID (Previous)**: `1500054` (the 193010Z log proves it started and finally ticked, and the 202514Z log proves it was subsequently replaced by PID `1950438`)
- **Supervisor PID (Current)**: `1950438`
- **Sync/Restart ISO Timestamp**: `2026-07-16T20:25:12Z` (Supervisor start log: `2026-07-17 04:25:15 +0800`)
- **Synchronization Method**: Normal manual call of `bash scripts/sync-dev-root.sh` by fleet, where the script's normal implementation updated the dev-root ref to origin/dev.

## Post-merge Inventory Run Receipt (Official Receipt)
- **Bootstrap Run ID**: `antigravity-bootstrap-20260716T2030Z`
- **Inventory Scan Timestamp**: `2026-07-16T20:30:50Z`
- **Inventory Metrics**:
  - Scanned Sources: `422` (411 legacy_ts_std, 10 legacy_ts_old, 1 active)
  - Logical entries yielded: `1,402,795`
  - Fold count: `240`
  - Mismatch count: `0`
  - Physical lines: `1,642,794`
  - Source classes: 411/10/1
  - Fold classes: 234/5/1
  - Line classes: 239x1000 + 1x999
- **Post-merge Evidence File Hashes**:
  - `evidence.md`: `112aa73ff758342ee905f9a54042e1f8a84028b44d860dd8829c65ac0a522d9e`
  - `summary.json`: `720914279393f6d401a59803547f7e03c9649d0b83ca8f7cbb1776606189bc20`
  - `manifest.json`: `2416b7fc1da07b963be4c3aaf7059159f647a59a5d02861fa05526b9902d1722`

## Pre-merge Comparison Baseline (Reference Only)
- **Bootstrap Run ID**: `antigravity-bootstrap-20260716T1859Z`
- **Inventory Scan Timestamp**: `2026-07-16T18:59:50Z`
- **Pre-merge Metrics**:
  - Scanned Sources: `422`
  - Logical entries yielded: `1,402,603`
  - Fold count: `240`
  - Mismatch count: `0`
- **Pre-merge Evidence File Hashes**:
  - `evidence.md`: `4dfd6e89150d30bb068d019a19fba5e7037d8b60307e23c262f2d2f1b80645b0`
  - `manifest.json`: `d8216d33777c7b629ad27d0b17a6c95acff78bb193ac2a33c9b3245a5c93e6c0`
  - `summary.json`: `0a7ef4fd57dc12b2b2683f219aabaa0eac3a0355ca8e66bd503444074a0dd8cd`

## Archive Consistency Receipt
- **Consistencies**: Verified that `421` non-active gzip archives are 100% byte-identical to the pre-merge manifest. The only difference is the growth of the active log file `ai-activity-log.jsonl` (line count from 1659 to 1851, size from 3,658,460 to 4,003,730 bytes, unique event IDs from 121 to 139) and the updated `scan_timestamp` (`2026-07-16T20:30:50Z`).

## Outbox Recovery Receipt
- **Saga Outbox Target Event ID**: `ai-status-event-96647dfb76bf1b7c8c1f657b78be8a4b2bc3ef3ef7adb86ee50f359289dfc99f`
- **Event Type**: `review_approved`
- **Task ID**: `OPS-WORKTREE-DELIVERY-CONTEXT-PLAN-001`
- **Recovery Action**: The target event is physically recorded once in the active log, and the corresponding target transaction has been cleared from the outbox. The duplicate historical ID `worker-commit-deb673789747a71068bff9f2578ad9f41d7b8253` which previously caused the reader outage was successfully bypassed. Unrelated subsequent transactions are unaffected.

## Governed Command Readback Receipt
- **Task Assignment Timestamp**: `2026-07-16T20:50:57Z`
- **Task Start Command Timestamp**: `2026-07-16T21:14:31Z`
- **Command Used**: `PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon AI_NAME=Antigravity python3 scripts/ai_status.py start OPS-ACTIVITY-AUDIT-LEGACY-OVERLAP-RECOVERY-001 "Starting postmerge evidence collection and acceptance validation"`

## Git Diff Check Verification
- **Diff Check Status**: The human-authored files (`README.md` and `receipts.md`) pass the `git diff --check` validation cleanly. The raw inventory output `evidence.md` contains a terminal blank line at the end-of-file, which is a known and byte-preserved exception required to keep the audited raw hashes intact.

## Remaining Work & Status
- **PR #3775**: The current post-merge evidence PR #3775 is pending Claude's review.
- **PR #3763**: The original task requires PR #3763 stale-worktree show/note/handoff proof.
