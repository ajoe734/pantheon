# Codex Repository Work Rules

These rules apply to Codex work in this repository. They are Codex's own
responsibility. Do not treat CI, hooks, reviewers, or the user as the first line
of enforcement for this workflow.

## Remediation Plan 2026-11-17 (Operator Decisions)

On 2026-11-17 the operator approved the remediation plan in
`docs/operations/remediation-plan-20261117.md`. It binds every coordinator
chatbox and every worker until the operator lifts it. Current state, owners and
open instances live in `docs/operations/problem-register.md`.

### Feature Freeze

Until the three trading personas complete one full day on hosted dev (enrolled,
daily cycle ticked, a decision used memory, a meeting was held, results were
written back), do not open new feature tasks. Open only tasks that belong to a
root-cause class in the problem register or that finish or repair work that is
already merged. An operator instruction in the current conversation that names
a specific feature overrides the freeze for that feature only.

### One Problem, One Class

Before opening a fix task, find the root-cause class in the problem register
and attach the incident to its owning work package. Do not open a one-off fix
for a symptom whose class already has an owner; add the instance to the
register. A new class needs a register entry before any task is opened.
Coordinators work inside one domain (see the register); acting on another
domain's files or on hosted outside your domain requires checking that
domain's register entry and live tasks first.

### Dev Deployment: Deployed Versus Accepted

For the dev environment only, a deployment is build, switch, and every expected
service running healthy at the expected version. Only an unhealthy stack rolls
back. Functional checks (management page crawl, paper lifecycle probe, Agora
smoke, paper baseline, market refresh) run after the switch; a failure marks the
release `deployed, not accepted` and opens a task for the owning chain. It does
not roll back. After any deploy or rollback, every expected steady service must
be verified running at the expected version; a matching `/bff/version` alone is
not proof. Staging and production keep gate-before-switch.

Do not add a new blocking functional gate to the dev deploy path. Until the
pipeline work packages in the register land, the pipeline still behaves the old
way; report a release as `accepted` only when its acceptance stage passed.

### Delivery States

Report every task and chain as exactly one of `merged`, `deployed` (served by
hosted dev according to the hosted manifest) or `accepted` (its acceptance probe
passed on hosted). Never report merged work to the operator as done or
complete. A feature chain ships with an executable read-only acceptance probe;
a manual Human/Ops hosted acceptance task is the fallback only when no probe
can exist.

### Runtime Budget

A task that adds or changes a recurring job, an emitter or a persistent writer
(file, table, queue, cache, image, temporary directory) must state in its
acceptance the cadence, size and count at hosted scale, the growth per day, the
cap, and how it is cleaned. Once the storage manifest exists the writer must be
registered there. Reviewers reject a change that adds a writer without this.

### Findings Outside Scope

A worker that finds a defect outside its task scope must record it as a
residual finding in the handoff, not only in evidence prose. The coordinator
attaches it to a register class or opens a class before the chain is closed.
"Not in scope" is not a disposition.

### Review Independence

The reviewer must be a different model family from the owner whenever another
family has capacity. Claude and Claude2 are one family, Codex and Codex2 are
one family, Antigravity and Antigravity2 are one family. Record the exception
in the task when no other family has capacity.

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

## Existing Task Cleanup And Resumption

When the operator withdraws obsolete or duplicate work, remove it from active
work using the existing `supersede` command; retain the archive and identify
which existing task owns any surviving obligation. Do not leave cancelled work
as `todo`, claim it was completed, or create replacement tasks unnecessarily.
Reconcile downstream dependencies with `dependency-contract`, preserving real
product prerequisites and source-writer ordering.

An explicit operator request to resume existing work must reach the canonical
state, not just a note: local Human/Ops uses `reopen` for operator-held `todo`
tasks as well as blocked work. Ordinary hosted dev dependency maintenance does
not require a new execution grant or MFA issuer and does not change execution
authority. Keep genuine tenant, secret, and paper/live boundaries intact.
Once authorized work is ready and a worker is available, let the existing
supervisor dispatch it and verify its queue/worker receipt. Report a real
remaining dependency or provider failure rather than requesting the same
operator authorization again. Do not add a second dispatcher or new gates.

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
Use repository `ajoe734/execute-plans` and its checkout on the current
development host, or a clean task worktree created from it. Do
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
Pantheon dev environment from an `execute-plans` commit.

Read `docs/deployment/vm-dev-staging-prod-management-plan.md` § 3.1 for the
single current environment identity and named GitHub deployment variables.
Do not copy hostnames or account paths from older runbooks or evidence.
The configured FE/BFF origins are not evidence that an accepted pair is served;
that requires the hosted manifest and deployment checks below.

Both former dev projects `pantheon-lupin-dev-20260719` and
`pantheon-benjamin-20260528` are retired. Do not deploy to or probe their old
addresses (`35.201.204.12`, `35.201.239.38`, `34.81.75.241`) or reuse
`/home/lupin/...` paths as current defaults. The old migration/cutover scripts
have been removed; historical source remains recoverable in Git.

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
release with exact identities passes stack health and its acceptance stage.
For dev the deployed/accepted split in "Remediation Plan 2026-11-17" applies;
staging and production remain gate-before-switch and rollback-safe.

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
to maintain local canonical development tasks. The local development bridge is
under `.orchestrator/development_bridge/`; it is not a product route or product
image dependency. Conversely,
supervisor or auto-worker health proves only that development tooling can
dispatch work; it does not prove that the product is deployed, ready, or usable.

The authoritative component map and acceptance boundaries are in
`docs/02-architecture/development-tooling-product-boundary.md`.

Management AI frontend work must start from `execute-plans`. Product UI may use
Pantheon BFF routes for product conversations and read-only diagnostics, but
must not create SA/SD packets, alter canonical tasks, prepare worktrees, or
write repository files. Do not route new Management AI development through
Lovable, and do not use `front-ai-trading-system` as a source checkout.

Development work uses a clean repository worktree plus local tooling:

- `.orchestrator/development_bridge/` for signed local task-packet transport;
- `scripts/human-ops-status.sh` and `scripts/ai_status.py` for local canonical
  task maintenance; and
- the repository task branch/PR workflow for source changes.

There are no product BFF routes for dev-doc generation, task-packet transport,
worktree preparation, or supervisor status. Do not add those routes back as
compatibility endpoints. Do not confuse `GET|POST /bff/assistant/tools/*` with
VM file-system access: product BFF actions are not shell, repository-read, or
repository-write capabilities.

OpenClaw-backed product diagnostics are reached through
`POST /bff/management/nl/ask`. `kernel_debug` is read-only. Product BFF and its
adapter never run source-writing repair work; implement code through the local
task worktree flow instead.

The local supervisor handoff path is
`.orchestrator/assistant-dev-packets/`. The supervisor must drain pending local
packets into `ai-task-archive/tasks/` before a task packet is considered
accepted by downstream workers. See
`docs/operations/management-ai-openclaw-dev-bridge.md` for the local-tooling
runbook.
