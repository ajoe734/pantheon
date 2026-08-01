# SUP-COMMAND-ROOT-SPLIT-HOTFIX-REVIEW-20260801

Owner: Codex  
Independent reviewer: Antigravity  
Delivery: Pantheon PR #4451  
Signed acceptance head: `d9a27f972fb8f9184a8dc15256d8ad8223948a8e`

Current PR head: `671a15e7dbf4ba278482fa7764a07d3972ae7237`

## Owner verification

The PR is bounded to `scripts/sync-dev-root.sh` and
`scripts/test_sync_dev_root.py`. The shell change makes a resolved split
between the live supervisor root and configured dev root an explicit restart
condition, even when neither code nor live configuration changed. The existing
PID-bound watchdog intent is still recorded before `SIGTERM`; intent-recording
failure still leaves the process running. A matching active root remains a
no-op when code and configuration are unchanged.

The two new regression tests passed together at the signed head
(`2 passed in 1.04s`). The complete five-test file passed there with
`SYNC_ACTIVE_ROOT=0` (`5 passed in 2.21s`), and
`bash -n scripts/sync-dev-root.sh` exited 0. Those commands ran from a fresh
detached worktree at the signed head after provisioning the repository-local
Python environment.

## Disclosed harness finding

An unmodified full-file run against today's advanced `origin/dev` produced
`3 passed, 2 failed`. This is not being hidden or pre-classified as acceptable.
The earlier
`test_sync_records_pid_bound_intent_before_stopping_live_supervisor` starts its
fake live process with the review worktree as its current directory. The script
therefore discovers the review checkout itself as the active supervisor root.
Because PR #4451 is currently one commit behind `dev`, the test syncs and
hard-resets that detached checkout to `origin/dev`; the two later tests then
invoke the old dev version of the shell script. Both later tests pass when run
before that mutation, and the full file passes when active-root syncing of the
runner checkout is disabled.

The PR subsequently gained test-only commit
`671a15e7dbf4ba278482fa7764a07d3972ae7237`, which starts the fake live process
from the temporary dev-root checkout. On a fresh detached worktree at that
head, the unmodified full file passes (`5 passed in 2.74s`) and shell syntax
validation still exits 0. The fix is bounded, but it changed the PR head after
the signed task packet bound acceptance to `d9a27f972...`.

Antigravity must provide the independent verdict, but must not approve or merge
`671a15e7...` under the unchanged signed binding. Human/Ops must either issue
auditable authorization for the new exact head or provide a replacement signed
packet. Codex/Codex2 do not provide the independent verdict or silently rewrite
the signed head.

## GitHub state at owner handoff

- PR head has changed from the signed SHA to `671a15e7...`.
- GitHub reports the PR mergeable but `BEHIND` current `dev` by six commits.
- Commit trailers, runtime mirror guard, Python packaging provision, smoke
  acceptance, and orchestrator forwarding checks are successful.
- No independent review decision is recorded yet; merge authority remains
  withheld pending exact-head authorization and independent review.

The machine-readable record is [evidence.json](evidence.json).
