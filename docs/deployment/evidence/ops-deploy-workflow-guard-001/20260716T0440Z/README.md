# OPS-DEPLOY-WORKFLOW-GUARD-001 evidence

Captured: 2026-07-16 ~04:38-04:40 UTC

## What this packet covers

The task brief asked for evidence that the shared deploy workflow stays
`active` across a proof run and that a non-owner deploy run can complete
while a proof run holds its own isolation. Two things are captured here
instead of a clean staged demonstration, and the gap is documented rather
than hidden:

1. **A live, still-running instance of the exact defect this task fixes**,
   observed on the box while doing this work (see
   `live-incident-20260716T0426Z.md`). It is independent of, and
   contemporaneous with, the `LOOP-PROD-FE-001` instance already recorded in
   the task brief — proof the pattern is not a single task's mistake.
2. **The new detection guard's real output** against that live incident
   (`disabled-shared-workflow-guard-sample.log`), showing it correctly
   reports both watched workflows as `disabled_manually` with their repo and
   workflow id.

## What is not captured here

A clean "dispatch a proof run, watch `nonprod-deploy` stay active, watch an
unrelated run complete concurrently" demonstration was not attempted. Both
shared workflows were already `disabled_manually` for the whole duration of
this task (via the unrelated live guard described above), so any new
dispatch would fail before it could demonstrate anything, and re-enabling
the workflow mid-incident to force a demo would race an unrelated task's
in-flight run without coordination. This is flagged as an open follow-up:
once the live guard exits (or is cleared by a human) and the workflow is
re-enabled, a `workflow_dispatch` to `dev` should be run to confirm the
`concurrency:` group and dev-environment-lease behave as documented in
`docs/conventions/GIT_WORKFLOW.md` § 9.1.

## Verification that was run

```
python3 -m pytest -q \
  scripts/test_dev_environment_lease.py \
  scripts/test_dev_environment_lease_deploy_contract.py \
  scripts/test_dev_environment_lease_guard.py \
  scripts/test_reap_hung_workers.py \
  scripts/test_check_shared_deploy_workflow_disabled.py
# 53 passed, 13 subtests passed
```

These confirm the sanctioned isolation primitives (concurrency group +
dev-environment-lease acquire/heartbeat/verify/release, contract-tested
against `nonprod-deploy.yml`'s `deploy-dev` job) and the new detection guard
behave as documented, independent of the live incident above.
