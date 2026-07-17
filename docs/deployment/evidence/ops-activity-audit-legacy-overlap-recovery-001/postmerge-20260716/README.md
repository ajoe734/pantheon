# Postmerge Evidence: OPS-ACTIVITY-AUDIT-LEGACY-OVERLAP-RECOVERY-001

This receipt documents the accepted merge, installation, inventory, and task activation for **OPS-ACTIVITY-AUDIT-LEGACY-OVERLAP-RECOVERY-001**. The inventory is accepted; final stale-worktree write proof remains gated as described in section 7 and in `closeout-20260717.md`.

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

The hashes above identify the immutable artifacts reviewed at PR #3775 exact
head `773f20f52a6badb8577dbe09faf51a0a85edb026`. The human-readable
evidence and summary were later corrected for the final adjacent tail chain,
pinned decompressed source hashes, and EOF whitespace; corrected hashes are
recorded in `closeout-20260717.md`.

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

This historical receipt was accepted during the exact-head reviews of PRs
#3773 and #3775. The packet does not retain the original typed outbox
before/after payload, so the 2026-07-17 closeout does not claim a new recovery
or reconstruct unavailable transaction bytes.

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

The task was subsequently reassigned by the supervisor to owner `Codex` with
reviewer `Claude`; the current central object remains the single active task
record for this ID.

---

## 7. Current Closeout Status (Not Completely Closed)

- **PR #3775**: Claude approved exact head `773f20f5`; the PR merged as `d651dbb99cc0870c4e9ac4d2815bdc116824c815`.
- **PR #3800**: Reader hardening merged as `a124a19bf525f93a8996651189845e5569c89ab4`, but the supervisor-provided command runtime for the 2026-07-17 recovery probe remained pinned to older ancestor `6d833e4b0aa5e07d1b151f0064f82c2d3368ce06` and therefore did not contain that merge.
- **Read-only stale-worktree probe**: governed `show` succeeded from detached stale merge `d4d0f693...` and returned the central `in_progress` task in 145.10 seconds; all six local sentinels remained byte-identical and the disposable worktree was removed.
- **Write proof**: governed `note` and owner-to-reviewer `handoff` were intentionally not run on the pre-hardening command runtime.
- **PR #3763**: still open and awaiting a fresh installed-runtime `show`/`note`/`handoff` proof plus Codex2 exact-head review.

The task must remain `in_progress` until the installed command runtime is
`a124a19bf...` or a descendant, the current inventory and stale write proof
pass, and the final evidence receives independent review.

---

## 8. Git Diff Check Verification
- **Diff Check Status**: The earlier terminal blank line in `evidence.md` was removed during the 2026-07-17 evidence consistency correction. The corrected task-scoped diff must pass `git diff --check` without exception.
