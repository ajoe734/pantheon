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
descriptor-relatively with `O_NOFOLLOW`. Git commands run from the already open
candidate-root descriptor, and root/config path identities are reopened and
compared before the immutable value is accepted. Parent replacement, symlink
components, leaf aliases, deletion and inode replacement therefore fail closed
without a component-check/open race.

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
| `.venv-pantheon/bin/python3 -m pytest -q scripts/test_promote_supervisor_runtime.py` | 91 passed in 12.45s |
| `.venv-pantheon/bin/python3 -m py_compile scripts/promote_supervisor_runtime.py scripts/test_promote_supervisor_runtime.py` | passed |
| `python3 scripts/git/check_commit_trailers.py --range origin/dev..HEAD --skip-merge` | passed through pre-evidence head `4a788b6c5a9532fea03a5bc946b791355c5110cd` |
| `git diff --check origin/dev...HEAD` | passed |
| `git merge-base --is-ancestor <rejected-head> HEAD` | exit 1 for `07316c73`, `77af55015`, `853a1778e`, and `4cd85c7a8` |

The tests separately cover production identity omission, direct/nested/missing/
out-of-prefix/symlink/deleted/swapped roots, basename mismatch, exact and spoofed
remotes, environment URL rewrite, accepted-dev membership and trusted tree,
tracked/index/worktree drift, every allowed generated path, forbidden ordinary
and ignored paths, exact/symlinked/swapped config paths, and independent path,
length, bytes, SHA-256 and inode drift.

The merged snapshot-invariant dependency `cd770e5dc` is an ancestor. The branch
is based on `dev` commit `941c15a34208e54e96cdd148ba3a5bfcd339abab`
without importing rejected implementation ancestry.

PR head `50ec4d122aa1e334f3bd564f1fe13182cb013449` was independently rejected by
Codex because ignored files, special index flags, Git environment rewriting and
parent-swap races remained fail-open. The canonical row then reassigned owner to
Codex and reviewer to Codex2. This refreshed evidence records the replacement
implementation and remains review-pending for a new exact-head Codex2 decision.

## Deliberate non-scope

This source-only slice does not inspect `/proc`, discover incumbent processes,
define launch/watchdog behavior, signal a process, write or repair config,
perform rollback, or promote a live runtime. No live service or config was
changed. Rollout is the eventual source merge; rollback is revert of that merge.

Independent exact-head Codex2 review remains required. This evidence does not
assert `review_approved` and makes no live-promotion claim.
