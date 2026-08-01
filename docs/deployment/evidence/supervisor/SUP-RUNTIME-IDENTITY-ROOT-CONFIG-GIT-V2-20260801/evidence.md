# SUP-RUNTIME-IDENTITY-ROOT-CONFIG-GIT-V2-20260801 evidence

Status: `review_pending`

Owner: Codex2

Reviewer: Codex

Repository / PR: `ajoe734/pantheon` / `#4443`

## Result

The task now supplies one frozen `CandidateRuntimeIdentity` for the exact
candidate root, Git commit/tree and live-config byte snapshot. Candidate Git
membership is checked against a fresh `dev` fetch in an isolated bare
repository, so a candidate-local forged `origin/dev` or URL rewrite cannot
authorize a commit. The candidate tree must match the same commit fetched from
trusted `dev`.

The candidate path must be the symlink-free direct child
`/home/lupin/pantheon-ci-deploy/command-runtimes/<40-lowercase-hex>`. The root
inode, HEAD, tree, origin and status are checked again during capture. The live
config must be the exact symlink-free
`/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json`;
its inode, bytes, byte length and SHA-256 are immutable comparison inputs.

## Cleanliness boundary

Tracked modifications, staged changes and deletions all fail closed. Untracked
files fail closed except for the finite lock-file set recorded in
`evidence.json` and direct generated task briefs matching
`.orchestrator/task-briefs/[a-z0-9][a-z0-9_-]*.md`. This does not allow
`.orchestrator/config.json`, state JSON, Python, nested task-brief content or a
blanket `.orchestrator/**` family.

## Verification

| Command | Result |
|---|---|
| `.venv-pantheon/bin/python3 -m pytest -q scripts/test_promote_supervisor_runtime.py` | 51 passed in 5.84s |
| `.venv-pantheon/bin/python3 -m py_compile scripts/promote_supervisor_runtime.py scripts/test_promote_supervisor_runtime.py` | passed |
| `git diff --check origin/dev...HEAD` | passed |
| `git merge-base --is-ancestor <rejected-head> HEAD` | exit 1 for `07316c73`, `77af55015`, `853a1778e`, and `4cd85c7a8` |

The merged snapshot-invariant dependency `cd770e5dc` is an ancestor. The branch
was refreshed through `dev` commit `941c15a34208e54e96cdd148ba3a5bfcd339abab`
without importing rejected implementation ancestry.

The earlier Antigravity review named head
`6b30e10af119351db78586f6f09339240500558f`, but its status event omitted
`REVIEW_PR` and `REVIEW_HEAD_SHA`, and `dev` advanced before merge. That
approval is not merge authority for this refreshed branch. The governed task
row subsequently reassigned review to Codex. Codex must independently review
and bind PR `#4443` to its final exact head.

## Deliberate non-scope

This source-only slice does not inspect `/proc`, discover incumbent processes,
define launch/watchdog behavior, signal a process, write or repair config,
perform rollback, or promote a live runtime. No live service or config was
changed. Rollout is the eventual source merge; rollback is revert of that merge.

Independent exact-head Codex review remains required. This evidence does
not assert `review_approved` and makes no live-promotion claim.
