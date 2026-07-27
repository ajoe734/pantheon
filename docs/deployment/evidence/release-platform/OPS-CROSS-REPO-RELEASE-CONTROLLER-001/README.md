# OPS-CROSS-REPO-RELEASE-CONTROLLER-001 Evidence

## Outcome

Pantheon dev delivery is now one explicit exact-pair transaction instead of an
automatic deploy after each fix or publish cut.

- Pantheon accepts only the exact current `ajoe734/pantheon:dev` commit.
- execute-plans accepts only the exact current
  `ajoe734/execute-plans:dev` commit.
- `agora_compat_manifest.py` generates one deterministic compatible ledger
  whose `release_candidate_id` binds both commits, both trees, and the exact
  compatibility-manifest digest.
- The ledger and the currently hosted FE/BFF rollback baseline are uploaded
  before the first live mutation.
- Pantheon deploys and probes the exact BFF, then
  `cross_repo_release_controller.py` dispatches and waits for the exact
  execute-plans gate and FE deploy runs.
- execute-plans does not upload a deployable artifact on an ordinary push and
  no longer deploys from `workflow_run`; both operations require the active
  Pantheon controller ledger.
- The FE candidate is built with strict live BFF routing and safe write
  defaults, probed before its atomic symlink switch, and self-rolls back on
  switch/probe failure.
- A rejected frontend gate or deploy causes Pantheon to restore the recorded
  BFF SHA under a fresh shared lease and prove both hosted FE and BFF identities
  equal the pre-switch baseline.

Publish snapshots remain immutable promotion inputs only. They do not trigger
this controller.

## Repository Boundary

Pantheon owns:

- `.github/workflows/nonprod-deploy.yml`
- `scripts/agora_compat_manifest.py`
- `scripts/cross_repo_release_controller.py`
- `scripts/compensate_cross_repo_release.sh`
- focused Python tests
- canonical nonprod delivery documentation and this evidence

execute-plans owns:

- `.github/workflows/pantheon-integration-gate.yml`
- `.github/workflows/pantheon-dev-fe-deploy.yml`
- `scripts/release-candidate.mjs`
- focused Vitest contracts

The execute-plans source remains in its own repository and clean task worktree:

```text
/tmp/pantheon-worker-worktrees/execute-plans/ops-cross-repo-release-controller-001
```

This task does not edit `docker-compose.yml`,
`scripts/deploy_nonprod_vm.sh`, or `execute-plans:scripts/deploy-dev-vm.sh`.
The runtime Compose manifest remains owned by `L12-MANIFEST-001`.

## Transaction and Compensation

```text
exact dev tips
  -> deterministic compatibility ledger
  -> seal ledger + hosted rollback baseline
  -> BFF deploy and exact version/health proof
  -> execute-plans gate builds/smokes exact FE against exact BFF
  -> FE deploy authenticates artifact and switches atomically
  -> accepted evidence

frontend gate/deploy rejection
  -> verify FE is still/restored at baseline
  -> reacquire shared environment lease
  -> restore baseline BFF with the existing deploy controller
  -> verify hosted FE deployment.json and BFF /bff/version
  -> upload compensation evidence and fail the rejected release run
```

The controller is fail-closed on branch-like identities, malformed SHAs or
digests, ambiguous workflow-run discovery, non-`dev` runs, stale `dev` tips,
soft-fail gates, out-of-order candidates, and missing rollback proof.

## Validation

Exact commands and results are recorded in `evidence.json`. The focused
baseline currently includes:

- Pantheon compatibility/controller/workflow tests;
- execute-plans candidate and workflow tests;
- Bash syntax, YAML parse, workflow-aware action lint, TypeScript typecheck,
  and production build;
- repository-boundary and prohibited-file diff checks.

No hosted deployment, VM mutation, workflow disable, unrelated run
cancellation, broker action, or real-capital action was performed while
implementing this controller. The first hosted run must be one explicit
post-merge release candidate, not an intermediate repair deployment.

## Review

Owner: Codex2  
Reviewer: Codex

Independent exact-head review and both repository PR/merge identities are
recorded in `evidence.json` before the governed task closeout.
