# SUP-COMMAND-RUNTIME-REFRESH-001 approval-binding bootstrap

Status: independently reviewed and merged through PR #4254; retained as the
bootstrap record and superseded for final acceptance by `evidence.json`.

Owner: Codex
Reviewer: Codex2
Repository: `ajoe734/pantheon`
Base: `dev`

## Why this bootstrap is necessary

The installed command runtime at redispatch was
`eecb96fa3826e8e3527a77da7f187a32b33c6c93`. A watchdog refresh moved the
live supervisor to accepted `dev` commit
`6692d51c9bc5a48ffcbaac8cf817b635351a7c9a`, but neither commit contains the
structured `REVIEW_PR` / `REVIEW_HEAD_SHA` approval binding required by
`OPS-PR-REVIEW-BEFORE-MERGE-GATE-001`.

That gate cannot merge first and provide its own command runtime afterward:
once its merge gate runs, an approval written by the old runtime has no exact
head binding and fails closed with `approval_head_binding_missing`.

This task therefore carries only the command-side bootstrap slice first:

- `scripts/ai_status.py` validates `REVIEW_PR`, full 40-hex
  `REVIEW_HEAD_SHA`, optional `REVIEW_BASE`, and optional
  `REVIEW_HEAD_BRANCH`;
- the validated identity is recorded as `review_binding` on both the task row
  and immutable `review_approved` event;
- an approval without binding remains valid for tasks that have no PR, while
  emitting the warning consumed by the later merge gate;
- the runtime-pin test fixture removes inherited authoritative-journal
  variables so an auto-worker test cannot read or write the live journal.

The slice deliberately does not include `task_review_merge_gate.py`,
`auto_integrator.py`, PR-helper policy, live config changes, or supervisor
dispatch changes. It composes with the separately reviewed
`OPS-PR-REVIEW-BEFORE-MERGE-GATE-001` branch after this runtime is active.

## Verification

| Command | Result |
|---|---|
| `REVIEW_PR=9999 REVIEW_HEAD_SHA=<40-b> REVIEW_BASE=inherited REVIEW_HEAD_BRANCH=task/inherited .venv-pantheon/bin/python3 scripts/test_ai_status.py` | 142 tests, OK |
| `.venv-pantheon/bin/python3 -m unittest scripts.test_ai_status.ReviewApprovedWorkflowTests` | 30 tests, OK |
| `.venv-pantheon/bin/python3 scripts/test_status_command_runtime_pin.py` | 7 tests, OK |
| `.venv-pantheon/bin/python3 -m py_compile scripts/ai_status.py scripts/test_ai_status.py scripts/test_status_command_runtime_pin.py` | OK |
| `git diff --check origin/dev...HEAD` | clean, exit 0 |

The runtime-pin suite initially exposed three fixture failures because the
background worker's live `PANTHEON_TASK_STATE_STORE_MODE` and
`PANTHEON_TASK_STATE_EVENT_LOG` leaked into its synthetic command root.
Removing those inherited variables from the fixture environment made the
normal, unmodified auto-worker invocation pass 7/7.

The approval workflow fixture now also removes inherited `REVIEW_PR`,
`REVIEW_HEAD_SHA`, `REVIEW_BASE`, and `REVIEW_HEAD_BRANCH` inputs. The
142-test run above deliberately supplied all four variables and proved that
the no-binding approval case remains unbound.

## Live intermediate state

The watchdog independently refreshed the supervisor before this worker issued
any stop or launch:

- incumbent PID after the handoff: `4138635`;
- incumbent root: `/home/lupin/pantheon-ci-deploy/dev-root-6692d51c9bc5`;
- incumbent HEAD/tree:
  `6692d51c9bc5a48ffcbaac8cf817b635351a7c9a` /
  `fa55d946a09b8311de7b37e4348405267378fecd`;
- live config sha256:
  `adab474b01b99630041cb06d565ae9dbfd7d52badc1d9e612b7cb8d4129de77e`;
- the three required `dev` contexts — Commit trailers, Runtime mirror guard,
  and Smoke acceptance — were successful on `6692d51c…`;
- five active workers kept their original issued command roots and their
  leases were extended after the handoff;
- authoritative task-state shadow was `ok: true`, `caught_up: true`, with
  equal projected and expected sha256
  `d6b16604fa0495ec5db3ff62b2b303c64696a1b4ed2471385e92b6b45f7e474b`.

This is only an intermediate runtime because `6692d51c…` does not contain the
approval binding.

## Review and continuation

Codex2 should review the exact bootstrap PR head while auto-merge remains
disabled. After that exact head merges into `dev`, the owner will:

1. select the resulting accepted `dev` commit and verify all required checks;
2. install and hand the supervisor over to that exact commit without editing
   the live config;
3. prove lease/queue parity and authoritative projection catch-up;
4. execute a rollback-to-prior-root and roll-forward drill;
5. replace the legacy partial manifest/narrative with final evidence;
6. request a final Codex2 review using the newly installed structured
   `REVIEW_PR` / `REVIEW_HEAD_SHA` binding.
