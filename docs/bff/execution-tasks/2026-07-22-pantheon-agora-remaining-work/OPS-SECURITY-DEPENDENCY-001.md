# OPS-SECURITY-DEPENDENCY-001 — Reconcile and remediate current dependency alerts

Priority: P1
Repository: `ajoe734/pantheon`
Merge target: `dev`
Owner: Codex
Reviewer: Claude
Depends on: `OPS-DISPATCH-LEASE-SYNC-001`

## Objective

Replace stale June dependency claims with a current, reachable-graph audit and
remove or fail-closed isolate every critical/high alert affecting Pantheon dev
or its build/runtime path.

## Current evidence

GitHub reports 20 open alerts: seven critical, three high, nine medium, and one
low. The current manifests include MLflow 3.10.1, Ray/RLlib 2.9.3, and Torch
2.12.0. Six npm alerts reference a historical
`execute-plans/package-lock.json` that is absent from current Pantheon `dev`.

## Owned scope

- `services/research/mlflow/requirements.txt`
- `services/research/rllib/requirements.txt`
- `services/research/finrl/requirements.txt`
- affected compose/profile/network/auth boundaries and focused tests
- Dependabot reconciliation evidence and dependency policy checks

## Required work

1. Re-query every open alert and bind it to a file reachable from current
   `dev`; dismiss removed historical-manifest alerts only with commit/path
   evidence.
2. Upgrade MLflow to a version covering the available 3.11.x fixes and run its
   service/container compatibility suite.
3. Upgrade Ray/RLlib where compatible and address advisories with no patched
   version through explicit token auth, non-public binding, dormant profile,
   and activation refusal. If safe activation cannot be proven, remove it from
   the default build/deploy graph.
4. Upgrade Torch when a compatible fixed build exists, or retain the low alert
   with a bounded risk record and make the vulnerable JIT path unreachable.
5. Add CI that inventories critical/high alerts in reachable manifests without
   treating deleted paths as active product dependencies.

## Acceptance

- No open critical/high alert remains in the accepted dev/default reachable
  dependency graph without a tested fail-closed isolation and reviewer-owned
  risk expiry.
- MLflow and Ray/RLlib are not exposed without authentication on a non-loopback
  interface; browser/DNS-rebinding and job-submission tests pass.
- Dormant research profiles remain disabled by default and cannot write
  registry, paper, canary, live, or capital state without their existing gates.
- Historical execute-plans alerts are reconciled using path/commit evidence,
  not by adding frontend source back into Pantheon.
- Dependency installs/builds, focused service tests, compose validation, and
  secret scan pass; PR merges to `dev`.

## Exclusions

- No production activation of MLflow, RLlib, FinRL, or EP5.
- No blanket alert dismissal and no disabling Dependabot/security checks.
- No execute-plans source or lockfile committed to Pantheon.

## Delivery evidence

- Task evidence:
  `docs/04/pantheon_agora_remaining_work_2026-07-22/archive/OPS-SECURITY-DEPENDENCY-001-evidence.md`
- The candidate pins MLflow 3.11.1, Ray/RLlib 2.54.0, Gymnasium 1.2.2, and
  Torch 2.13.0 CPU, and keeps all research services behind tested dormant,
  fail-closed boundaries.
- The six dismissed npm alerts refer only to the frontend mirror removed from
  Pantheon `dev` by `834318190c90962e024ee9b82243cf31a742f441`.
- Remediation PR #3968 merged as
  `983c2a84b2f4947f848ffbbd0f7f230d6c8d5875`; the least-privilege alert-read
  workflow fix in PR #3969 merged as
  `1f51fc82f918412bd5654a2872bb48df716a4f82`.
- Dependency Alert Reachability run `29946794630` passed on the delivered
  `dev` merge, including alert query, candidate reconciliation, and evidence
  artifact upload.
