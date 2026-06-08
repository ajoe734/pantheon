# Codex Repository Work Rules

These rules apply to Codex work in this repository. They are Codex's own
responsibility. Do not treat CI, hooks, reviewers, or the user as the first line
of enforcement for this workflow.

## Completion Definition For Repo Changes

When Codex modifies repository files, the work is not complete until Codex has
finished the repository development flow:

- inspect `git status -sb`, current branch, and remote before editing;
- use a clean task branch or clean worktree when the current checkout is dirty,
  detached, on a default branch, or shared with live workers;
- keep unrelated user, worker, generated, and runtime-state changes out of the
  commit;
- run relevant local validation;
- stage only the intended files;
- commit with the repository's required subject and trailers;
- push the branch;
- open a PR;
- wait for required repository checks when they are visible;
- merge the PR when the user asked for completed delivery and policy allows it;
- report the PR number, merge commit, and validation in the final answer.

If any step is blocked, Codex must say exactly which step is blocked and why.
Never present local-only edits, a restarted process, or passing local tests as a
completed repository change.

## Live Repair Rule

For urgent supervisor, worker, auth, or runtime repair, Codex may perform the
smallest live rescue first. Codex must explicitly label that action as temporary
live repair, then put the exact code or configuration change through branch,
commit, push, PR, checks, and merge in the same turn.

This is not optional process paperwork. It is part of the engineering task.

## Frontend Repository And Dev Hosting

The active frontend system is `execute-plans`, not `front-ai-trading-system`.
Use repository `ajoe734/execute-plans` and local checkout
`/home/lupin/code/execute-plans` or a clean task worktree created from it. Do
not create, revive, mirror to, or assign new work to `front-ai-trading-system`;
that name is legacy-only and must not be used for current development.

As of 2026-06-08 the `execute-plans` remote has `main` as its frontend delivery
base; there is no remote `dev` branch. If a frontend dev branch is later
created, update this file and `docs/frontend/execute-plans-dev-hosting.md`
before routing work to it.

Do not treat Lovable publish status as the dev frontend host or as the release
truth for Pantheon dev. Lovable URLs may remain historical evidence or an
external reference, but current dev frontend delivery must be served by the
Pantheon dev environment from an `execute-plans` commit. The intended dev host
is Pantheon-owned, for example
`https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`, with the BFF target
`https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`.

When deploying or validating the dev frontend, ensure the frontend build uses
`VITE_BFF_MODE=live`, `VITE_BFF_BASE_URL` pointing at the dev BFF,
`VITE_BFF_FALLBACK=strict`, and safe write defaults unless the operator
explicitly enables real writes. Also ensure the dev BFF
`PANTHEON_BFF_CORS_ORIGINS` includes the Pantheon-owned frontend origin before
browser smoke tests. See `docs/frontend/execute-plans-dev-hosting.md`.
