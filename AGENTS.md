# Codex Repository Work Rules

These rules apply to Codex work in this repository. They are Codex's own
responsibility. Do not treat CI, hooks, reviewers, or the user as the first line
of enforcement for this workflow.

## Chatbox Work Classification And Dispatch Authority

### Explicit Operator Bypass

When the operator explicitly tells the current chatbox to implement a stated
scope directly or to bypass a named repository workflow, the chatbox may do so.
This applies to component, cross-component, supervisor, fleet, task-state,
deployment, architecture, cleanup, and integration work. The chatbox does not
need to route that scope through the supervisor, auto-workers, assistant dev
bridge, canonical task materialization, or task packets.

The bypass is limited to the scope and workflow the operator explicitly names;
it must not be inferred for unrelated work. The chatbox must not both dispatch
and implement the same scope unless the operator explicitly requests both.
Repository delivery requirements below still apply.

A repository workflow bypass changes how work is performed; it does not permit
forged credentials or evidence, secret disclosure, hand-edited canonical task
or queue JSON, or unrequested destructive, production, or capital-affecting
actions.

### Operator-Authorized Development Tooling Delivery

When the operator explicitly authorizes development-tooling cleanup,
refactoring, or repair to be delivered directly, the chatbox must validate and
commit the scoped change, then integrate and push it directly to `dev`. It must
not create a PR, request a reviewer, wait for a canonical reviewer attestation,
or treat this work as product delivery merely because the files live in this
repository.

If protected-branch checks can only be published by GitHub Actions from a pull
request event, the chatbox may create a **mechanical** PR labelled
`delivery:tooling` solely to carry those checks and merge it itself after they
pass. That PR is not a product review: it must not request or wait for a
reviewer, canonical attestation, review-proof tag, or additional approval.
The chatbox must not manufacture any of those artifacts. Once the required
checks pass, it merges the exact validated head to `dev` and reports that the
PR was a branch-protection transport constraint.

This exception covers the supervisor, `.orchestrator/`, development scripts,
development workflows, and their focused tests and documentation. It preserves
clean-worktree, scope, validation, commit-trailer, and current-`dev` rebase
requirements. It does not authorize product-runtime, production, secret, or
capital-affecting changes unless the operator explicitly includes them.

### Default Coordination

Without an explicit direct-implementation or bypass instruction, the Pantheon
supervisor remains the routine implementation dispatcher for coordination,
system-wide, fleet, and integration work. The chatbox may inspect, diagnose,
plan, create governed task packets, and monitor execution without silently
taking over implementation.

Configured agent identities, including `Codex` and `Codex2`, remain distinct.
Account relationships, quota grouping, and reviewer eligibility must follow
current configuration and task-scoped live authentication or quota evidence.
Do not infer identity equivalence, capacity equivalence, or reviewer
ineligibility from configured agent names alone.

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

## Development-Stage Approval Posture

During active development, Codex should keep work moving without asking the
operator for repeated approval on ordinary repo, validation, and dev-deploy
commands. If the sandbox blocks a necessary command for git, GitHub CLI,
package install/build/test, Docker Compose dev services, local smoke tests,
Playwright, curl probes, or Pantheon-owned dev deployment, Codex should use the
available approved prefix or request tool escalation directly with a concise
justification and continue after approval. Do not pause the task just to ask a
separate chat question for these normal development actions.

This posture does not authorize reckless actions. Codex must still ask before
unrequested destructive commands, secret disclosure, credential rotation,
production/live trading or capital-affecting changes, broad filesystem deletion,
or any action outside the stated development objective.

## Frontend Repository And Dev Hosting

The active frontend system is `execute-plans`, not `front-ai-trading-system`.
Use repository `ajoe734/execute-plans` and local checkout
`/home/lupin/code/execute-plans` or a clean task worktree created from it. Do
not create, revive, mirror to, or assign new work to `front-ai-trading-system`;
that name is legacy-only and must not be used for current development.

`execute-plans` is a separate repository and must never be materialized as an
`execute-plans/` directory inside a Pantheon checkout. Do not copy, mirror, or
commit frontend source, frontend tests, or frontend build configuration here.
Frontend artifacts named `execute-plans/...` in cross-repository task packets
refer to paths in `ajoe734/execute-plans`, not paths under this repository.

Do not ask the operator to press Lovable publish for Pantheon dev frontend
delivery. Do not wait on Lovable connector authorization, Lovable publish
status, or `https://pantheon-dev.lovable.app` before continuing Pantheon dev
work. The current dev frontend is deployed from a GitHub-visible
`execute-plans` commit to Pantheon-owned hosting.

As of 2026-07-13 the `execute-plans` remote and GitHub default branch use
`dev` as the frontend delivery base. Route new frontend task PRs to `dev`
unless the branch policy is deliberately changed in both this file and
`docs/frontend/execute-plans-dev-hosting.md`. The remote `main` branch still
exists but has diverged from `dev`; do not use `main` as an implicit delivery
target or treat a main-only merge as deployed dev evidence.

Do not treat Lovable publish status as the dev frontend host or as the release
truth for Pantheon dev. Lovable URLs may remain historical evidence or an
external reference, but current dev frontend delivery must be served by the
Pantheon dev environment from an `execute-plans` commit. The intended dev host
is Pantheon-owned, for example
`https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io`, with the BFF target
`https://pantheon-lupin-dev-bff.35.201.204.12.sslip.io`.

As of 2026-07-19 the prior GCP project `pantheon-benjamin-20260528` is
suspended. The replacement dev VM is `pantheon-lupin-dev` in project
`pantheon-lupin-dev-20260719`, external IP `35.201.204.12`, and the backend
source checkout is `/home/lupin/pantheon`. Do not deploy dev to the suspended
project, the old `35.201.239.38` host, or `/home/lupin/code/pantheon`.

Historical verified dev deployment, 2026-06-08:

- `pantheon` BFF/adapter base: merge commit
  `22b89367a56cdbb4fb8a7345fc7c4ad1d293a118` on `dev`.
- `execute-plans` frontend base: merge commit
  `8337b19a0cf6ac41aa2a4c2fa3950f6af3a87abf` on `main`.
- Hosted frontend bundle path on the dev VM:
  `/var/www/pantheon-dev-fe/`.

Current delivery truth must be read from the hosted deployment manifest and
GitHub deployment/check evidence. A newer remote commit or a successful build
does not prove that the hosted symlink serves it. If the hosted manifest has
unsafe write defaults, lacks exact FE/BFF identities, or points to a candidate
whose deployment workflow failed, treat the deployment as unaccepted until a
gate-before-switch and rollback-safe release passes.

When deploying or validating the dev frontend, ensure the frontend build uses
`VITE_BFF_MODE=live`, `VITE_BFF_BASE_URL` pointing at the dev BFF,
`VITE_BFF_FALLBACK=strict`, and safe write defaults unless the operator
explicitly enables real writes. Also ensure the dev BFF
`PANTHEON_BFF_CORS_ORIGINS` includes the Pantheon-owned frontend origin before
browser smoke tests. See `docs/frontend/execute-plans-dev-hosting.md`.

## Management AI / OpenClaw Dev Work

### Development Tooling Is Not Product Runtime

Keep these authority domains separate:

- Development tooling owns canonical development tasks, supervisor scheduling,
  worker leases, and local Human/Ops task maintenance. Its entry points are the
  V2 TaskStore, supervisor, auto-workers, `scripts/ai_status.py`, and
  `scripts/human-ops-status.sh`.
- Product runtime owns business APIs and product data/readiness. This includes
  the operator BFF business routes, source ingestion, lifecycle projection, and
  the hosted `execute-plans` frontend.
- Delivery infrastructure owns exact-version deployment and hosted identity
  evidence. It does not become task authority or product truth.

Do not require a product BFF login, product control mode, or product readiness
to maintain local canonical development tasks. Local Human/Ops status commands
are the only task-ingress path; there is no development bridge. Conversely,
supervisor or auto-worker health proves only that development tooling can
dispatch work; it does not prove that the product is deployed, ready, or usable.

The authoritative component map and acceptance boundaries are in
`docs/02-architecture/development-tooling-product-boundary.md`.

Management AI frontend work must start from `execute-plans`. Product UI may use
Pantheon BFF routes for product conversations and read-only diagnostics, but
must not create SA/SD packets, alter canonical tasks, prepare worktrees, or
write repository files. Do not route new Management AI development through
Lovable, and do not use `front-ai-trading-system` as a source checkout.

Development work uses a clean repository worktree, `scripts/human-ops-status.sh`,
`scripts/ai_status.py`, and the repository task branch/PR workflow. The local
Human/Ops status command is the only canonical task ingress.

There are no product BFF routes for dev-doc generation, task-packet transport,
worktree preparation, or supervisor status. Do not add those routes back as
compatibility endpoints. Do not confuse `GET|POST /bff/assistant/tools/*` with
VM file-system access: product BFF actions are not shell, repository-read, or
repository-write capabilities.

OpenClaw-backed product diagnostics are reached through
`POST /bff/management/nl/ask`. `kernel_debug` is read-only. Product BFF and its
adapter never run source-writing repair work; implement code through the local
task worktree flow instead.

The supervisor consumes only already-materialized canonical tasks. It does not
accept task packets, drain a local inbox, or depend on product BFF routes.
