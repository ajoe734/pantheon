# Postmerge Evidence: OPS-ACTIVITY-AUDIT-LEGACY-OVERLAP-RECOVERY-001

This receipt document verifies the postmerge acceptance, installation, inventory validation, and task activation for **OPS-ACTIVITY-AUDIT-LEGACY-OVERLAP-RECOVERY-001**, noting the remaining items listed at the end of this document. All steps have been processed in accordance with the repository's strict collaboration and validation policies.

---

## 1. PR #3773 Merge Commit Details
- **PR Source Branch**: `task/OPS-ACTIVITY-AUDIT-LEGACY-OVERLAP-RECOVERY-001`
- **Target Branch**: `dev`
- **Merge Commit SHA**: `64844eef7e87c63c955c98fa95579992aa3af5e2`
- **Merge Timestamp**: `2026-07-16T20:08:23Z` (Local: `2026-07-17 04:08:23 +0800`)

---

## 2. Dev-Root Supervisor Installation (Before & After)
This time, a normal manual call of `bash scripts/sync-dev-root.sh` was invoked by fleet, and the script's normal implementation updated the dev-root ref to origin/dev, synchronizing and updating the dev-root supervisor:
- **Before Installation State**:
  - **Commit SHA**: `af912820eda9f6ca54f9b4333766ae30a9830120`
  - **Supervisor Process PID**: `1500054` (evidence: `.orchestrator/logs/supervisor-watchdog-restart-20260716T193010Z.log` proves it started and finally ticked, and `.orchestrator/logs/supervisor-watchdog-restart-20260716T202514Z.log` proves it was subsequently replaced by PID `1950438`)
- **After Installation State**:
  - **Commit SHA**: `64844eef7e87c63c955c98fa95579992aa3af5e2`
  - **New Supervisor Process PID**: `1950438` (started; evidence in `.orchestrator/logs/supervisor-watchdog-restart-20260716T202514Z.log`)

---

## 3. Unique Inventory Run (Post-merge Receipt)
The official post-merge inventory run was performed to validate the postmerge state:
- **Inventory Timestamp**: `2026-07-16T20:30:50Z`
- **Bootstrap Run ID**: `antigravity-bootstrap-20260716T2030Z`
- **Scan Source Count**: `422` files total (411 `legacy_ts_std`, 10 `legacy_ts_old`, 1 active `ai-activity-log.jsonl`)
- **Log Folds Identified**: `240` folds total (234 `valid_legacy_rotation` standard 1,000-line folds, 5 `incident_lineage` standard 1,000-line folds, and 1 `pinned_exception` 999-line fold; line-count classes: 239x1000 + 1x999)
- **Inventory Metrics**:
  - Physical Lines: `1,642,794`
  - Logical Entries: `1,402,795`
  - Mismatch Count: `0`
- **Post-merge File SHA-256 Hashes**:
  - `evidence.md`: `112aa73ff758342ee905f9a54042e1f8a84028b44d860dd8829c65ac0a522d9e`
  - `summary.json`: `720914279393f6d401a59803547f7e03c9649d0b83ca8f7cbb1776606189bc20`
  - `manifest.json`: `2416b7fc1da07b963be4c3aaf7059159f647a59a5d02861fa05526b9902d1722`

### Pre-merge Comparison Baseline
For comparison, the pre-merge baseline run was performed prior to merging:
- **Pre-merge Timestamp**: `2026-07-16T18:59:50Z`
- **Pre-merge Bootstrap Run ID**: `antigravity-bootstrap-20260716T1859Z`
- **Pre-merge Metrics**: Scanned Sources: 422, Folds: 240, Mismatches: 0, Logical Entries: 1,402,603
- **Pre-merge Baseline File SHA-256 Hashes**:
  - `evidence.md`: `4dfd6e89150d30bb068d019a19fba5e7037d8b60307e23c262f2d2f1b80645b0`
  - `manifest.json`: `d8216d33777c7b629ad27d0b17a6c95acff78bb193ac2a33c9b3245a5c93e6c0`
  - `summary.json`: `0a7ef4fd57dc12b2b2683f219aabaa0eac3a0355ca8e66bd503444074a0dd8cd`

---

## 4. Archive Consistency Verification
Comparing the post-merge `manifest.json` against the pre-merge comparison baseline `manifest.json`:
- **Archive Invariance**: All **421 non-active gzip log archives** match exactly.
- **Active Log Growths**: The only modifications are limited to the active `ai-activity-log.jsonl` file (size increased from `3,658,460` to `4,003,730` bytes, line count from `1,659` to `1,851`, and unique logical event IDs from `121` to `139`) as new events were appended in the interim.
- **Fold Consistency**: The duplicate log fold count remains exactly `240`, proving no new overlaps or data corruption occurred.

---

## 5. Outbox Recovery Verification (Exactly-Once)
The pending outbox target event is `ai-status-event-96647dfb76bf1b7c8c1f657b78be8a4b2bc3ef3ef7adb86ee50f359289dfc99f` (type: `review_approved`, task: `OPS-WORKTREE-DELIVERY-CONTEXT-PLAN-001`).
Note that `worker-commit-deb673789747a71068bff9f2578ad9f41d7b8253` was a duplicate historical ID that caused the legacy logical reader outage, rather than the pending outbox event itself.
After the recovery run, the target event is physically written exactly once in the active log, and the target transaction was cleared from the outbox. This outbox recovery resolves the block on this specific transaction, and does not imply the global outbox will remain permanently empty, as subsequent unrelated transactions can process normally.

---

## 6. Official Task Assignment and Start Readback
The task was formally initialized and advanced through the central governed command mechanism:
- **Assignment Event**:
  - **Timestamp**: `2026-07-16T20:50:57Z`
  - **Action**: Task assigned to Owner `Antigravity` with Reviewer `Claude`. Status set to `todo`.
- **Start Event**:
  - **Timestamp**: `2026-07-16T21:14:31Z`
  - **Command**:
    ```bash
    PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon AI_NAME=Antigravity python3 scripts/ai_status.py start OPS-ACTIVITY-AUDIT-LEGACY-OVERLAP-RECOVERY-001 "Starting postmerge evidence collection and acceptance validation"
    ```
  - **Result**: Successfully logged and read back. Status transited to `in_progress`.

---

## 7. Remaining Work & Status (Not Completely Closed)
This task and its evidence are not completely finished/unblocked:
- **PR #3775**: The current post-merge evidence PR #3775 is pending Claude's review.
- **PR #3763**: The original task requires PR #3763 stale-worktree show/note/handoff proof.

---

## 8. Git Diff Check Verification
- **Diff Check Status**: The human-authored files (`README.md` and `receipts.md`) pass the `git diff --check` validation cleanly. The raw inventory output `evidence.md` contains a terminal blank line at the end-of-file, which is a known and byte-preserved exception required to keep the audited raw hashes intact.
