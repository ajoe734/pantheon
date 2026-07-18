# OPS-DEPLOY-WORKFLOW-GUARD-001 implementation recheck 2026-07-18T03:08Z

## Implemented boundary

Anchor commit `625ceb21d` adds the missing durable controls:

- Pantheon manual deploys use an environment-scoped workflow-level
  `queue: max`; push runs pass through that level and preserve the existing
  latest-push cancellation semantics in job-level `dev-auto` and
  `staging-auto` groups.
- Every orchestrator provider receives `.orchestrator/bin` first in its
  delivery `PATH`. The repository-owned `gh` shim rejects workflow disable
  and run cancel/force-cancel operations, including protected raw Actions API
  endpoint forms, before delegating allowed commands to the pinned real CLI.
- Branch CI scans worker-facing instructions for copy-pastable shared deploy
  mutations and runs executable queue, CLI, and disabled-state contracts.
- The shared-workflow detector now fails closed for non-object JSON and any
  readable state other than `active`; only confirmed `disabled_manually` can
  be repaired by its operator-only `--enable` mode.
- Guard parsing treats group-level flags between `run` / `workflow` and their
  mutating action as equivalent syntax, and auto workers cannot create or
  import mutable `gh` aliases to hide an operation from argument inspection.
- Provider-specific `HOME` values no longer affect real-CLI discovery, and a
  repository-owned `BASH_ENV` hook restores the guarded bin after login-shell
  profile changes. Current-repository placeholders and protected numeric REST
  repository routes fail closed.

The worker-facing incident pseudocode was redacted. The governed historical
evidence remains intact in earlier evidence packets.

## Validation

- `./scripts/run-acceptance.sh smoke` passed in an isolated Python virtual
  environment after composing current `dev`.
- Shared deploy policy suite: `38 passed, 24 subtests passed`.
- Orchestrator adapter fallback policy: `14 passed`.
- Orchestrator common suite: `90 passed`.
- Static worker-instruction scan, shell syntax, Python compilation, YAML
  contract parse, commit trailers, and `git diff --check` passed.
- Real login-shell resolution selected the repository shim even after a
  user-local PATH prepend; placeholder and numeric-route mutation probes
  returned the guard's non-zero exit without invoking the fake real CLI.
- GitHub run `29628222918` exposed a CI ordering error: the new pytest suite
  ran before Stage-0 installed pytest. `scripts/run-acceptance.sh` now keeps
  the dependency-free scan first and runs the executable suite after the
  Stage-0 baseline; the isolated smoke above validates that exact order.

At capture time both shared workflows were active:

- `ajoe734/pantheon` workflow `269991390`: `active`;
- `ajoe734/execute-plans` workflow `292028803`: `active`.

## STOP gate remains closed

This packet does not authorize a proof dispatch or auto-merge.

- Corrective merge `f9875d6f87905e4900edfd4deb218fa65c3b1c3f`
  still lacks an independent exact-merged-tree acceptance that explicitly
  includes final-head review artifact commit
  `e07001009f6e4799b14721d083bba1416c82c38a`.
- Prior Pantheon proof run `29626781890` failed before deployment because the
  managed dev checkout contained unrelated dirty planning files. Both hosted
  probes were skipped. This task does not own or clean those files.
- The valid execute-plans success remains run `29500642299`; it must not be
  replaced merely to manufacture fresher evidence.

After the independent corrective review and remote checkout reconciliation,
the remaining acceptance is a Pantheon-only governed proof that reaches
terminal success, reports both previously unhealthy services healthy, runs
all required hosted probes, and shows both shared workflows stayed active
without cross-cancellation.
