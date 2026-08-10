# SUP-AGENT-ALIAS-GUARD-LIVE-PROMOTION-20260731 evidence

Task: Promote merged alias reassignment guard into live supervisor runtime

Current owner: `Codex`

Current reviewer: `Claude`

Review decision: **pending**

Current delivery state: **blocked at split-incumbent-entrypoint preflight; no
mutation performed**

## Outcome first

The alias guard from PR #4430 is present in the bytes used by the live
supervisor, but the governed runtime promotion task is not yet safe to close.

The current live process is the sole supervisor PID `1393542`. Its argv
executes
`command-runtimes/5877b64425c8d6aede147d6cbbc6fbb9e228c259/.orchestrator/supervisor.py`,
while its cwd is the mutable `/home/lupin/pantheon-ci-deploy/dev-root`. The
entrypoint checkout has since advanced to `0305c861...`, so its directory
basename no longer matches its Git HEAD.

The merged promotion helper therefore failed closed before config, signal,
launch, or rollback:

```text
incumbent_supervisor_process_identity_immutable = false
Captured live config does not bind the exact canonical supervisor entrypoint
```

No restart, TERM, launch, config writer, canonical state edit, queue edit, or
provider change was performed.

## What changed since the 2026-08-01 evidence

The old evidence correctly preserved the deleted-cwd incident, Human/Ops
rescue, temporary target process, and later watchdog fallback. It is retained
in
[`raw/revalidation-preflight-20260801T150539Z.json`](raw/revalidation-preflight-20260801T150539Z.json)
as incident history, but its live readback is stale.

The former rollback-helper blocker is resolved:

| Dependency | Current durable result |
|---|---|
| `SUP-RUNTIME-PROMOTION-SNAPSHOT-INVARIANTS-20260801` | done / completed |
| `SUP-RUNTIME-IDENTITY-ROOT-CONFIG-GIT-V2-20260801` | done / completed |
| `SUP-RUNTIME-PROMOTION-ROLLBACK-TRANSACTION-OPERATOR-V2-20260802` | done / completed |
| `SUP-RUNTIME-PROMOTION-FAILURE-MATRIX-INTEGRATION-OPERATOR-V2-20260802` | done / completed |

The current blocker is narrower and was discovered only after those layers
were live: the incumbent uses separate cwd and argv entrypoint roots.

## Exact newer accepted candidate

The clean current candidate is:

| Field | Exact value |
|---|---|
| Root | `/home/lupin/pantheon-ci-deploy/command-runtimes/0305c861f54c4082060120afdfbc012622e5ac0a` |
| HEAD | `0305c861f54c4082060120afdfbc012622e5ac0a` |
| Tree | `249eecac3a5a1a3eb41e80006afee566bfabdd92` |
| Origin | `https://github.com/ajoe734/pantheon.git` |
| Status | clean |
| Basename = HEAD | true |
| Accepted `origin/dev` | true |
| PR #4430 merge `012dab969...` is ancestor | true |

The live entrypoint, clean candidate, and `origin/dev` supervisor source all
have Git blob `4a6136f28ec2a23f77ca3060b0cdec279005b129`. This proves
the running source bytes include the guard even though the incumbent root
identity is not acceptable for a transaction.

The live implementation of `known_agent_display_names` excludes dispatch
slots, empty names, and display names containing `legacy alias`. The four
tests introduced by PR #4430 pass against the current composed source:

```text
.venv-pantheon/bin/python3 -m pytest -q \
  .orchestrator/test_supervisor.py::ProcessQueueDispatchGuardTests::test_known_agent_display_names_filters_legacy_aliases \
  .orchestrator/test_supervisor.py::WorkerReassignmentTests::test_reassign_after_worker_failure_never_assigns_legacy_alias \
  .orchestrator/test_supervisor.py::WorkerReassignmentTests::test_reassignment_with_legacy_alias_config_uses_real_persist_task_reassignment \
  .orchestrator/test_supervisor.py::WorkerReassignmentTests::test_normalize_mainline_task_assignment_never_assigns_legacy_alias

4 passed in 1.78s
```

## Current read-only preflight

Two discover-only probes ran with the merged transaction helper.

The first used the live command root and rejected it because basename
`5877b644...` did not match HEAD `0305c861...`.

The second used the clean exact `0305c861...` candidate. Candidate identity
passed, but incumbent process identity and the immutable launch contract failed
because the captured config/argv/cwd use the split legacy layout.

Passing gates from the second probe:

- clean SHA-named candidate identity and accepted-dev ancestry;
- readable config and canonical state files;
- healthy running supervisor, PID alive, and singleton lock held;
- authoritative projection `ok=true`, `caught_up=true`, no error, with
  equal `64f17f42...` projected and expected hashes;
- fresh loop sequence;
- worker/queue/worktree-lease parity with no duplicate active workers;
- readiness for active providers `antigravity2`, `codex1-2`, and
  `codex2-2`;
- no orphaned in-progress task.

Failing gates:

| Gate | Result |
|---|---|
| Immutable incumbent process identity | fail: config does not bind the exact canonical entrypoint |
| Immutable governed launch contract | blocked because incumbent identity is unavailable |
| Eligible for promotion | false |
| Safe to mutate | false |

The live config SHA-256 was
`61c8aed05539a7fbd445e6696aff1bc3caf8b7995cf1ae59487e92f88f068aea`
before and after the readback. The historic 2026-07-31 hash
`728a6d90...` is no longer current; this task does not rewrite current bytes
to imitate the old baseline.

## Active blocker and resume gate

Canonical task
`SUP-RUNTIME-V10-MUTABLE-INCUMBENT-SPLIT-ENTRYPOINT-20260810` is
`in_progress`, owned by `Antigravity2` and reviewed by `Codex2`. Its
acceptance reproduces this exact PID, cwd, argv root, candidate, and preflight
failure. It is source-only and explicitly reserves rollout retry for a separate
governed supervisor dispatch.

This task may resume live rollout only after:

1. the split-entrypoint task is independently reviewed, merged to `dev`, and
   present in a fresh clean SHA-named candidate;
2. a new `--discover-only` run passes candidate, incumbent, launch-contract,
   projection, lease, provider, and config-CAS gates;
3. the governed `--promote --bootstrap-mutable-incumbent` transaction is run,
   with automatic rollback on any failed postcheck;
4. post-promotion readback proves one immutable supervisor, three fresh loops,
   equal authoritative hashes, lease parity, provider readiness, and unchanged
   transaction-bound config bytes.

Overall result: **blocked preflight, no live mutation**.
