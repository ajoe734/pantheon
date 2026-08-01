# SUP-COMMAND-ROOT-SPLIT-HOTFIX-REVIEW-20260801

Owner: Codex  
Independent reviewer: Antigravity  
Delivery: Pantheon PR #4451  
Exact head: `d9a27f972fb8f9184a8dc15256d8ad8223948a8e`

## Owner verification

The PR is bounded to `scripts/sync-dev-root.sh` and
`scripts/test_sync_dev_root.py`. The shell change makes a resolved split
between the live supervisor root and configured dev root an explicit restart
condition, even when neither code nor live configuration changed. The existing
PID-bound watchdog intent is still recorded before `SIGTERM`; intent-recording
failure still leaves the process running. A matching active root remains a
no-op when code and configuration are unchanged.

The two new regression tests pass together (`2 passed in 1.04s`). The complete
five-test file passes with `SYNC_ACTIVE_ROOT=0` (`5 passed in 2.21s`), and
`bash -n scripts/sync-dev-root.sh` exits 0. All commands ran from a fresh
detached worktree at the exact PR head after provisioning the repository-local
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

Antigravity must independently decide whether this harness self-mutation blocks
the hotfix. Codex/Codex2 do not provide the independent verdict.

## GitHub state at owner handoff

- PR head is unchanged at the required SHA.
- GitHub reports the PR mergeable but `BEHIND` current `dev` by one commit.
- Commit trailers, runtime mirror guard, Python packaging provision, smoke
  acceptance, and orchestrator forwarding checks are successful.
- No independent review decision is recorded yet; merge authority remains
  withheld.

The machine-readable record is [evidence.json](evidence.json).
