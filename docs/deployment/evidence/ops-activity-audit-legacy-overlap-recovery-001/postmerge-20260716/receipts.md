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

## Historical Post-merge Inventory Run Receipt
- **Scope**: The following values describe only the 2026-07-16 snapshot. They
  do not prove current global ordering, gap-free history, or conservation.
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
- **Original PR #3775 Exact-Head Evidence File Hashes**:
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
- **Recovery Action**: The target event is physically recorded once in the active log, and the corresponding target transaction has been cleared from the outbox. The duplicate historical ID `worker-commit-deb673789747a71068bff9f2578ad9f41d7b8253` which previously caused the reader outage was successfully bypassed. This receipt is limited to that historical transaction and makes no claim about later transaction health.
- **Receipt Boundary**: PRs #3773 and #3775 accepted this historical result; the packet does not retain the original typed outbox before/after payload, so no later transaction reconstruction is claimed.

## Pinned 999-Line Exception Identity Receipt
- **T1237Z gzip SHA-256**: `ad7dd174e0278a3c21b10024cd227f0d138052dd0945bc3b24159538d87ed6c5`
- **T1237Z decompressed SHA-256**: `8435543b845639383471bd3a3d1b1d1642bb0944649b5e2a4ffe1ad5ad9a4e57`
- **T1239Z gzip SHA-256**: `d211e27bc5337c8eff200e14d48800f949658e6c8b43d9fd22e54ea8c77061da`
- **T1239Z decompressed SHA-256**: `da6a102178c82fb4eca8d0794ed5b419f0c97770e0ad63542dde0033e7efa3ff`
- **Overlap**: 999 lines, 5,325,808 bytes, SHA-256 `0a3b56f720a5aa493d8968edfff8e32e0df98e410f6334d6790f10a06019f247`

## Governed Command Readback Receipt
- **Task Assignment Timestamp**: `2026-07-16T20:50:57Z`
- **Task Start Command Timestamp**: `2026-07-16T21:14:31Z`
- **Command Used**: `PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon AI_NAME=Antigravity python3 scripts/ai_status.py start OPS-ACTIVITY-AUDIT-LEGACY-OVERLAP-RECOVERY-001 "Starting postmerge evidence collection and acceptance validation"`

## Git Diff Check Verification
- **Diff Check Status**: The earlier `evidence.md` terminal blank line was removed; the corrected task-scoped diff is required to pass without exception.

## Remaining Work & Status
- **2026-07-18 P0 boundary**: the raw installed-runtime snapshot is retained, but its `0404Z -> 1754Z` legacy disjoint edge has no durable authority. Any current inventory acceptance based on filename order is withdrawn; see `p0-disjoint-edge-fail-closed-20260718.md`.
- **PR #3775**: Claude approved exact head `773f20f5`; merged as `d651dbb99cc0870c4e9ac4d2815bdc116824c815`.
- **PR #3800 / runtime install**: hardening merged as `a124a19bf525f93a8996651189845e5569c89ab4`; the dispatched command runtime remained `6d833e4b...`, so installed-head acceptance must be rerun after sync/restart.
- **Stale proof**: read-only `show` succeeded from detached stale merge `d4d0f693...`; local sentinels remained unchanged. `note` and `handoff` remain intentionally pending for the installed hardening runtime.
- **PR #3763**: still requires the complete stale-worktree write proof and Codex2 exact-head approval.
