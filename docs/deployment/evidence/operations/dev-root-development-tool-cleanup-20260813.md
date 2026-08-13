# Dev-root development-tool cleanup — 2026-08-13

## Authority and dispatch correction

The operator explicitly directed the current chatbox to perform development-tool
cleanup directly and not route it through the Pantheon supervisor. The previously
materialized `OPS-STALE-DEV-ROOT-RETIRE-20260813` task was therefore superseded
through the local Human/Ops canonical CLI. Its auto-worker process was terminated,
and PR #4838 was closed without merge.

PR #4838 is not accepted as source truth. It proposed an unconditional
`git clean -fdx`, which could delete unrelated ignored local configuration, and
its evidence incorrectly stated that `dev-root` was not registered as a Git
worktree. The direct replacement keeps the cleanup narrowly scoped and records
the real worktree registration.

## Physical cleanup readback

At `2026-08-13T13:29:50Z`:

- `/home/lupin/pantheon-ci-deploy/dev-root` HEAD was
  `12a8dd18a78ec7bf1716b4b80226152ad3ffd533`.
- `origin/dev` in that checkout resolved to the same SHA.
- `git status --porcelain=v1 -uall` returned zero entries.
- A scoped `git clean -ndx` preview for assistant packets, worker evidence,
  task briefs, and the two supervisor/status locks returned zero entries.
- `git ls-files --others --ignored --exclude-standard` and
  `git ls-files --others --exclude-standard` returned zero files for those
  scoped paths.
- The same paths contain 2,214 tracked repository files. They are current source
  and retained; they are not stale deployment residue.
- `dev-root` remains an intentionally registered detached Git worktree. The
  registration is not a stale file and was not removed.

The obsolete BFF canonical-mutation source files deleted by
`22f16d6450b024388a7012ed7c921e1fb85174c6` are absent from `origin/dev` and from
the refreshed `dev-root`:

- `.orchestrator/canonical_mutation_assertion.py`
- `scripts/test_canonical_mutation_assertion.py`

## Live runtime isolation

At the same readback, supervisor PID `318563` ran from
`/home/lupin/pantheon-ci-deploy/command-runtimes/12a8dd18a78ec7bf1716b4b80226152ad3ffd533`.
No process or open file was observed under the mutable `dev-root`. Product
services were not restarted or redeployed by this cleanup.

## Source hardening

`scripts/sync-dev-root.sh` now removes only these known ephemeral pathspecs:

- `.orchestrator/assistant-dev-packets`
- `.orchestrator/evidence`
- `.orchestrator/task-briefs`
- `.orchestrator/status-derived-views.lock`
- `.orchestrator/supervisor.lock`

Cleanup runs only when a live immutable incumbent is independently resolved at a
different repository root. An unresolved incumbent or an active mutable
`dev-root` causes cleanup to skip. Failures are fatal instead of being swallowed.
Unrelated ignored files such as `.env`, local notes, and local secret files are
preserved; tracked files under the scoped directories are also preserved.

Validation:

```text
bash -n scripts/sync-dev-root.sh
/home/lupin/pantheon/.venv/bin/python -m pytest scripts/test_sync_dev_root.py -q
.........                                                                [100%]
9 passed in 9.07s
git diff --check
```

The hardened script was also executed against the real checkout at
`2026-08-13T13:34:33Z`, pinned to the already accepted SHA so this cleanup
could not advance source or trigger a runtime promotion. It reported:

```text
ACTIVE_ROOT_SPLIT_PROTECTED: live supervisor pid=318563 runs from command-runtimes/12a8dd18..., not dev-root
dev-root at 12a8dd18a, behind 12a8dd18a... by 0
development-tool residue already clean in /home/lupin/pantheon-ci-deploy/dev-root
done (updated=0 source_advance=0 config_drift=0 root_split=1 promotion=no-op-current-root)
```

Post-run HEAD remained `12a8dd18a78ec7bf1716b4b80226152ad3ffd533`,
Git status remained empty, and scoped ignored residue remained zero.
