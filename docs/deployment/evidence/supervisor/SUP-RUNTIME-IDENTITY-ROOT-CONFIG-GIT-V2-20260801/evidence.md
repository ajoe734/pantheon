# SUP-RUNTIME-IDENTITY-ROOT-CONFIG-GIT-V2-20260801 evidence

Status: `review_pending`

Owner: Codex

Reviewer: Human/Ops

Repository / PR: `ajoe734/pantheon` / `#4443`

## Result

Every production `capture_promotion_snapshot` call now builds and revalidates
one frozen `CandidateRuntimeIdentity`; omission or any identity exception adds a
failed `candidate_runtime_identity_immutable` invariant and makes promotion
ineligible. The CLI preserves the lexical candidate path so a mutable alias is
not resolved away before the guard runs.

Candidate-root and live-config traversal opens every absolute path component
descriptor-relatively with `O_NOFOLLOW`. The snapshot records and compares the
identity of every live-config component from `/` through the exact config leaf,
so replacing a parent with a normal directory and hardlinking the same config
inode back at the same lexical path still fails closed.

The candidate root must contain a direct `.git` directory, never a symlink or
gitfile. Git commands receive descriptor-bound `GIT_DIR`, `GIT_WORK_TREE`,
`GIT_COMMON_DIR`, `GIT_OBJECT_DIRECTORY`, and `GIT_INDEX_FILE` values. The guard
also rejects commondir and alternates pointers, config includes, cross-filesystem
metadata, hardlinked config/HEAD/index files, and symlinks anywhere in the full
objects tree as well as refs or info metadata. Loose-object fanout directories
are included rather than only `objects/pack`, so an external object alias cannot
satisfy the candidate identity.

Trusted `dev` is fetched into a fresh bare repository from the fixed Pantheon
URL after all inherited `GIT_*` configuration, URL rewrite, object, protocol and
transport variables have been removed. The candidate commit must exist in and
be an ancestor of that fetched `dev`, and its trusted tree must equal the local
HEAD tree.

## Cleanliness boundary

Tracked modifications, staged changes and deletions fail closed. The guard also
rejects `skip-worktree` and `assume-unchanged` index flags, verifies index versus
HEAD and worktree versus index, and enumerates ignored entries as well as normal
untracked entries. Every non-allowlisted ignored or untracked path is rejected.

The finite allowlist remains the exact lock-file set recorded in
`evidence.json` plus direct generated task briefs matching
`.orchestrator/task-briefs/[a-z0-9][a-z0-9_-]*.md`. Path matching is necessary
but not sufficient: every allowed entry is opened from the candidate descriptor
one no-follow component at a time and its leaf must be a regular file on the
candidate filesystem. Directory markers are rejected before normalization, so
an ignored `evil.md/` directory or a directory/symlink substituted for an exact
lock path fails closed. The allowlist does not admit `.orchestrator/config.json`,
state JSON, Python, nested task-brief content, ignored injected files, or a
blanket `.orchestrator/**` family.

## Verification

| Command | Result |
|---|---|
| `.venv-pantheon/bin/python3 -m pytest -q scripts/test_promote_supervisor_runtime.py` | 108 passed in 25.80s |
| `.venv-pantheon/bin/python3 -m py_compile scripts/promote_supervisor_runtime.py scripts/test_promote_supervisor_runtime.py` | passed |
| `python3 scripts/git/check_commit_trailers.py --range origin/dev..HEAD --skip-merge` | passed through the composed task history before this evidence refresh |
| `git diff --check origin/dev...HEAD` | passed |
| `git merge-base --is-ancestor <rejected-head> HEAD` | exit 1 for `07316c73`, `77af55015`, `853a1778e`, and `4cd85c7a8` |

The tests separately cover production identity omission, direct/nested/missing/
out-of-prefix/symlink/deleted/swapped roots, basename mismatch, exact and spoofed
remotes, environment URL rewrite, accepted-dev membership and trusted tree,
tracked/index/worktree drift, every allowed generated path, forbidden ordinary
and ignored paths, external `.git`, linked-worktree gitfiles, commondir,
alternates, object/index/ref symlinks, external config includes, hardlinked Git
config, Git metadata inode replacement, exact/symlinked/swapped config paths,
same-leaf-inode parent replacement, and independent path, length, bytes, SHA-256
and inode drift. The latest separate deny probes cover allowlisted lock and
task-brief symlinks, ignored directories substituted for each allowed-file
family, and an external symlinked loose-object fanout.

The merged snapshot-invariant dependency `cd770e5dc` is an ancestor. The branch
now composes `origin/dev` commit
`79ba3f431127bf9718697d2ba9e9ddce97969ec3` through merge commit
`03e167cda5932fd5b1637e90d674e74d42b27c3c`, without rewriting reviewed
history or importing rejected implementation ancestry. The delivery diff
against that base remains exactly the two promotion scripts and these two
task-scoped evidence files.

PR head `42cbc8f73df4d3521f8de9c1ef19a2348c6ba6ed` was reopened by Human/Ops
because it was behind current `dev`. The implementation itself is unchanged:
the current-base composition retains the previously reviewed descriptor-bound
regular-file validation and complete objects-tree scan, and the full focused
matrix still passes. This refreshed evidence remains review-pending for a new
exact-head Human/Ops decision.

## Deliberate non-scope

This source-only slice does not inspect `/proc`, discover incumbent processes,
define launch/watchdog behavior, signal a process, write or repair config,
perform rollback, or promote a live runtime. No live service or config was
changed. Rollout is the eventual source merge; rollback is revert of that merge.

Independent exact-head Human/Ops review remains required. This evidence does not
assert `review_approved` and makes no live-promotion claim.
