# OPS-PUBLISH-ENGINE-UNIFICATION-001 Evidence

## Outcome

Pantheon and execute-plans now expose the same nightly publish contract:

- `now` cuts at most one immutable publish branch and release tag.
- `check` returns `10` when the integration branch has not advanced.
- success emits `publish_branch=<ref>` and `release_tag=<ref>`.
- latest-tag selection is bounded at the Git producer with
  `for-each-ref --count=1`; no `head` or `tail` closes the pipe early.
- a repeated `now` is a no-op and a repeated `check` returns `10`.

The two hourly workflows are publish-only. Neither dispatches a deployment.
This keeps an immutable snapshot from being mistaken for exact accepted
Pantheon/execute-plans pair admission. The downstream nonprod deploy keeps its
independent fail-closed exact-pair gate; the cross-repository release
controller is owned by the dependent
`OPS-CROSS-REPO-RELEASE-CONTROLLER-001` task.

## Incident Reproduction

The repair is grounded in these GitHub runs:

| Repository | Run | Evidence |
|---|---:|---|
| Pantheon | `30277673883` | `Cut publish snapshot` exited `141` before producing output. |
| execute-plans | `30278537124` | `Cut publish snapshot` exited `141` at the same helper boundary. |
| Pantheon | `30272335370` → `30272369330` | A successful cut dispatched nonprod deploy, which then failed at `Enforce exact Agora pair before any dev switch`. |

The committed regression creates 12,000 release tags. The historical
`git for-each-ref | head -1 | awk` pipeline deterministically returns `141`;
the repaired helper then creates one snapshot, treats the second `now` as a
no-op, returns `10` from the second `check`, and proves that only one remote
branch/tag pair exists.

## Repository Boundaries

Pantheon owns:

- `scripts/git/nightly_publish.sh`
- `.github/workflows/nightly-publish-cut.yml`
- `scripts/git/test_nightly_publish.sh`
- `scripts/test_nightly_publish_cut.py`
- canonical Git/deploy documentation and this evidence

execute-plans owns:

- `scripts/git/nightly_publish.sh`
- `.github/workflows/nightly-publish-cut.yml`
- `scripts/git/test_nightly_publish.sh`

No execute-plans source is copied into Pantheon. The frontend work is on
`task/OPS-PUBLISH-ENGINE-UNIFICATION-001`, based on execute-plans `dev`, in
the clean worktree
`/tmp/pantheon-worker-worktrees/execute-plans/ops-publish-engine-unification-001`.

Delivery PRs:

- Pantheon: [PR #4255](https://github.com/ajoe734/pantheon/pull/4255),
  merged as `33e1c4d64e4accceab4d803e7b4ce2324f44306a`
- execute-plans:
  [PR #557](https://github.com/ajoe734/execute-plans/pull/557), merged as
  `cbc16830a096077f978b4efe499dd8fa85f166f2`

## Validation

The exact commands and final results are recorded in `evidence.json`.
Highlights:

- Pantheon focused suite: `78 passed`.
- execute-plans: `4` promotion tests, `19` deploy-safe tests, typecheck, and
  production build passed.
- both 12,000-tag publish regressions passed.
- shell syntax, Python compile, workflow/evidence parsing, cross-repo contract
  markers, repository boundary, and diff checks passed.

The initial local frontend build intentionally failed closed when the
CI-provided public Identity Platform values were absent; the recorded rerun
used format-valid non-secret test values and passed. No hosted deployment, VM
mutation, workflow disable, or unrelated run cancellation was performed.

## Review

Codex2 approved Pantheon head
`885f9c34d2d83d08e1564089395fe337ea99b033` and execute-plans head
`ec9ca4d7eb255b72366c43076ad477a3daa77fcf` after independently rerunning both
12,000-tag regressions, the Pantheon 78-test suite, execute-plans 4+19 tests,
typecheck, and the production build. The review also confirmed the equivalent
producer/consumer contract, absence of deployment dispatch, and explicit
publish-only boundary.

Both reviewed heads and their merge commits are ancestors of the corresponding
`origin/dev` refs. No hosted deployment was run because deployment admission
and switching are outside this task.
