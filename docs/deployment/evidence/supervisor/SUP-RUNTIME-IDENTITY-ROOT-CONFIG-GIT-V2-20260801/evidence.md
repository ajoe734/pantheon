# SUP-RUNTIME-IDENTITY-ROOT-CONFIG-GIT-V2-20260801 evidence

Status: `review_pending`

Owner: Codex

Reviewer: Codex2

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
metadata, hardlinked config/HEAD/index files, and symlinks in refs, info, or pack
metadata. A linked worktree or external Git metadata alias cannot satisfy the
candidate identity.

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
`.orchestrator/task-briefs/[a-z0-9][a-z0-9_-]*.md`. It does not allow
`.orchestrator/config.json`, state JSON, Python, nested task-brief content,
ignored injected files, or a blanket `.orchestrator/**` family.

## Verification

| Command | Result |
|---|---|
| `.venv-pantheon/bin/python3 -m pytest -q scripts/test_promote_supervisor_runtime.py` | 103 passed in 21.41s |
| `.venv-pantheon/bin/python3 -m py_compile scripts/promote_supervisor_runtime.py scripts/test_promote_supervisor_runtime.py` | passed |
| `python3 scripts/git/check_commit_trailers.py --range origin/dev..HEAD --skip-merge` | passed through pre-evidence head `2c751b122bbc28642d3ab09a3efb7edf760840d8` |
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
and inode drift.

The merged snapshot-invariant dependency `cd770e5dc` is an ancestor. The branch
is based on `dev` commit `941c15a34208e54e96cdd148ba3a5bfcd339abab`
without importing rejected implementation ancestry.

PR head `bcda6e10e9b41abc021cd443c5900e62d7d227a2` was independently rejected by
Codex2 because external `.git` metadata, linked-worktree gitfiles, and a
same-leaf-inode live-config parent replacement remained fail-open. This
refreshed evidence records the descriptor-bound replacement and remains
review-pending for a new exact-head Codex2 decision.

## Deliberate non-scope

This source-only slice does not inspect `/proc`, discover incumbent processes,
define launch/watchdog behavior, signal a process, write or repair config,
perform rollback, or promote a live runtime. No live service or config was
changed. Rollout is the eventual source merge; rollback is revert of that merge.

Independent exact-head Codex2 review remains required. This evidence does not
assert `review_approved` and makes no live-promotion claim.
