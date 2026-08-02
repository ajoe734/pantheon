# SUP-RUNTIME-IDENTITY-PROCESS-BINDING-V2-20260801 evidence

Status: `review_pending`

Owner: Codex

Reviewer: Human/Ops

Repository / PR: `ajoe734/pantheon` / pending

## Result

`capture_promotion_snapshot` now requires a separately reported
`incumbent_supervisor_process_identity_immutable` invariant in addition to the
merged immutable root/config/Git invariant. A missing, unreadable, ambiguous,
or mismatched incumbent makes the snapshot ineligible; the older health and
"some lock is held" checks cannot satisfy the new invariant.

Discovery enumerates procfs and requires exactly one live candidate whose
complete argv equals the captured live config's `watchdog.supervisor_command`.
The command must contain exactly one canonical
`<candidate>/.orchestrator/supervisor.py` entrypoint and exactly one `--config`
whose following value is the captured live-config path. Executable identity is
resolved independently from argv.

The selected process is bound to PID plus `/proc/<pid>/stat` starttime. The
generation is re-read before and after field reads and before every identity
comparison. Vanished, reused, unreadable, or zombie processes fail closed. The
process cwd realpath/device/inode and descriptor-bound Git HEAD/tree must match
the immutable candidate root. Only `PANTHEON_COMMAND_ROOT`,
`PANTHEON_COMMAND_RUNTIME_SHA`, and `PANTHEON_STATUS_ROOT` are retained from the
process environment, and each must match the candidate commit and captured
live-config status root.

## Admission-lock bracket

The admission evidence is the lifetime singleton
`<status-root>/.orchestrator/supervisor.lock`, not the per-loop
`runtime-admission.lock`. Before and after discovery, the implementation binds:

- no-follow regular-file device/inode, one-link identity, content length and
  SHA-256, mtime, and ctime;
- the unique `/proc/locks` row's kernel lock id, `FLOCK`, `ADVISORY`, `WRITE`,
  and full-file `0..EOF` range;
- the identical file PID, kernel owner PID, and owner process starttime.

The whole lock identity must remain equal around process discovery and the
second immutable candidate revalidation. Merely observing a held lock is not
accepted.

## Evidence privacy

The snapshot does not emit the full argv or environment. It records the argv
argument count and a boundary-preserving SHA-256, the validated entrypoint and
config paths, and only the three allowlisted environment values. Errors never
include non-allowlisted environment names or values.

## Verification

| Command | Result |
|---|---|
| `.venv-pantheon/bin/python3 -m pytest -q scripts/test_promote_supervisor_runtime.py` | 145 tests passed |
| `.venv-pantheon/bin/python3 -m py_compile scripts/promote_supervisor_runtime.py scripts/test_promote_supervisor_runtime.py` | passed |
| `git diff --check origin/dev...HEAD` | passed at implementation head `1103c674d267400fb1d96669d1bc2f053629b0a2` |
| `python3 scripts/git/check_commit_trailers.py --range origin/dev..HEAD --skip-merge` | passed |
| `git merge-base --is-ancestor <rejected-head> HEAD` | exit 1 for both rejected PR #4437 heads and all three rejected PR #4438 heads |

The 145-test file includes the 120 merged root/config/Git regressions plus
separate process cases for exact success, zero/multiple candidates, wrong
config, wrong entrypoint, extra argv, stale/reused PID, zombie, deleted/wrong
cwd, wrong cwd commit/tree, each allowlisted environment mismatch, unreadable
proc data, wrong executable, lock owner-generation mismatch, kernel/file lock
generation drift, allowlist-only environment extraction, the real current-test
process's temporary `flock` owner/starttime binding, production invariant
requirement, and candidate revalidation inside the lock bracket.

## Deliberate non-scope

This source-only slice does not launch, terminate, signal, restart, roll back,
or promote a process. It does not change watchdog or governed launch behavior,
live or repository config, canonical task-state JSON, provider policy, product
controllers, or live services. No live discovery probe was run and this
evidence makes no live runtime or promotion claim.

Rollout is source merge only. Rollback is revert of the eventual task merge
commit. Independent exact-head Human/Ops review remains required before merge;
this evidence does not assert `review_approved`.
