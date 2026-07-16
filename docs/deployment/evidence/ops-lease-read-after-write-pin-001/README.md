# OPS-LEASE-READ-AFTER-WRITE-PIN-001 Evidence

Date: 2026-07-16
Owner: Codex2
Reviewer: Claude

## Scope

This corrective preserves PR #3754 and PR #3755 as incident history and applies
the follow-up acceptance hardening on top of current `origin/dev`.

Implemented behavior:

- `.github/workflows/nonprod-deploy.yml` pins `.lease-controller` to protected
  Pantheon merge commit `ddf4d0d5d33a848b3c86e3be2f6713e2ad9c0524`.
- The pinned controller file checksums are verified before every use:
  - `scripts/dev_environment_lease.py`:
    `52276793f99162fc7ca307a1370addd8d99478208ebf7beb67eab23b97b83048`
  - `scripts/run_with_dev_environment_lease.sh`:
    `f3995a2baedc2ff47178a0de8ad1952096df4de508d5a47c8e0042a151ab7ea8`
- The broad PR #3755 initial verify retry loop is absent.
- Only the immediate post-acquire verify in `Start identity-bound lease
  heartbeat` carries `--initial-visibility-wait-seconds 15` and
  `--initial-visibility-poll-seconds 1`.
- Heartbeat, guarded deploy steps, release, and other lease verifies remain
  strict.
- No Pantheon proof deployment was dispatched from this task.

## Process Failure Record

PR #3757 merged as `87c2f7e50bc66b23e16436aa32775fcf2fedd8bb` at
2026-07-16T14:10:04Z without GitHub or governed Claude review, and its
implementation commit still recorded `Reviewer: pending`. This evidence treats
that merge as incident history only, not task completion.

Claude must audit merge `87c2f7e50bc66b23e16436aa32775fcf2fedd8bb` and rerun
the named controller/workflow negative tests before approval. No deploy proof
should be dispatched from this corrective task.

## Acceptance Coverage

Controller unit tests cover:

- exact expired predecessor, then current blob succeeds;
- foreign active replacement fails on the first read;
- wrong predecessor SHA fails on the first read;
- missing or malformed predecessor SHA fails on the first read, even with
  initial visibility opt-in;
- missing initial-visibility opt-in fails on the first read;
- bounded visibility timeout fails closed;
- invalid initial wait/poll bounds fail before remote reads.

Workflow contract tests cover:

- exact controller commit pin;
- controller script and wrapper checksums matching the pinned commit files;
- only the immediate post-acquire verify carrying initial visibility flags;
- broad `for attempt in $(seq 1 50)` initial verify loop removal;
- existing lease identity, deploy guard, release ordering, shared workflow
  state, action pinning, and no-cross-cancel contracts.

## Validation

Run locally on `task/OPS-LEASE-READ-AFTER-WRITE-PIN-001` after composing
`origin/dev` `135d266b8de855c187d3307d41d86376833d728c`. The exact PR head
for governed Claude approval is recorded in PR #3760 and the owner handoff,
so this evidence file does not try to self-reference a commit that changes
when evidence text is updated:

```text
python3 -m pytest -q scripts/test_dev_environment_lease.py scripts/test_dev_environment_lease_guard.py scripts/test_dev_environment_lease_deploy_contract.py scripts/test_deploy_nonprod_bff_strict_auth_default_contract.py scripts/test_check_shared_deploy_workflow_disabled.py
# 63 passed, 19 subtests passed

python3 -m py_compile scripts/dev_environment_lease.py

python3 -c 'import yaml; yaml.safe_load(open(".github/workflows/nonprod-deploy.yml", encoding="utf-8"))'

python3 scripts/check_shared_deploy_workflow_disabled.py

git diff --check
```
