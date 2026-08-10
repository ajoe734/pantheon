# SUP-AGENT-ALIAS-GUARD-LIVE-PROMOTION-20260731 evidence

Task: Promote merged alias reassignment guard into live supervisor runtime

Current owner: `Codex`

Current reviewer: `Claude`

Review decision: **pending**

Current delivery state: **blocked at same-commit rollback non-alias preflight;
no mutation performed**

## Outcome first

The alias guard from PR #4430 is present in the bytes used by the live
supervisor, but the governed runtime promotion task is not yet safe to close.

The current live process is the sole supervisor PID `1393542`. Its argv
executes
`command-runtimes/5877b64425c8d6aede147d6cbbc6fbb9e228c259/.orchestrator/supervisor.py`,
while its cwd is the mutable `/home/lupin/pantheon-ci-deploy/dev-root`. The
entrypoint checkout has since advanced to `0305c861...`, so its directory
basename no longer matches its Git HEAD.

The split-entrypoint helper merged through PR #4724 and now captures this
legacy process layout. The next read-only preparation gate failed before the
admission lock, config, signal, launch, or rollback because the immutable
rollback materializer reused the clean candidate directory:

```text
ValueError: Candidate runtime equals the rollback runtime
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

The split-root blocker is now resolved by PR #4724 (reviewed head
`6920db97...`, merge `8b77e779...`). The current blocker is narrower: candidate
and incumbent share accepted commit `0305c861...`, and rollback materialization
must still choose a distinct immutable path.

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

The regular `--discover-only` probe ran from the clean SHA-named
`8b77e779...` helper runtime. It reconfirmed candidate identity, runtime health,
projection equality, lease parity, provider readiness, and the expected legacy
split-layout rejection because that CLI mode deliberately does not enable
`--bootstrap-mutable-incumbent`.

To preserve gate-before-switch semantics, the same merged backend then ran only
its preparation stage with `bootstrap_mutable_incumbent=True` against the clean
exact `0305c861...` candidate. This stage does not acquire the runtime-admission
lock, record restart intent, write config, signal, or launch a process.

The merged split-root code advanced past legacy incumbent capture. It then
resolved the rollback checkout to the same
`command-runtimes/0305c861...` directory as the candidate and failed its
explicit non-alias guard:

```text
ValueError: Candidate runtime equals the rollback runtime
```

Passing gates from the regular probe plus the bootstrap capture:

- clean SHA-named candidate identity and accepted-dev ancestry;
- readable config and canonical state files;
- healthy running supervisor, PID alive, and singleton lock held;
- authoritative projection `ok=true`, `caught_up=true`, no error, with
  equal `471709bb...` projected and expected hashes;
- fresh loop sequence;
- worker/queue/worktree-lease parity with no duplicate active workers;
- readiness for active providers `codex1-2` and `codex2-1`;
- no orphaned in-progress task.

Failing gates:

| Gate | Result |
|---|---|
| Split incumbent capture | pass in bootstrap preparation |
| Distinct immutable rollback runtime | fail: resolved rollback root aliases candidate |
| Candidate and rollback launch contracts | blocked before derivation |
| Eligible for promotion | false |
| Safe to mutate | false |

The live config SHA-256 was
`61c8aed05539a7fbd445e6696aff1bc3caf8b7995cf1ae59487e92f88f068aea`
before and after the readback. The historic 2026-07-31 hash
`728a6d90...` is no longer current; this task does not rewrite current bytes
to imitate the old baseline.

## Active blocker and resume gate

Canonical task
`SUP-RUNTIME-V10-MUTABLE-INCUMBENT-SPLIT-ENTRYPOINT-20260810` is complete and
archived after PR #4724 merged as `8b77e779...`.

The recommended narrow follow-up ID is
`SUP-RUNTIME-V10-SAME-COMMIT-ROLLBACK-NONALIAS-20260810`, with owner
`Antigravity2` and reviewer `Codex2`. This worker could not admit that task:
the governed active-lease guard correctly rejected a cross-task `assign` while
this worker is leased to the live-promotion task. Human/Ops or the supervisor
must create the source-only follow-up through the normal task packet path.

This task may resume live rollout only after:

1. the same-commit non-alias rollback follow-up is admitted, independently
   reviewed, and merged to `dev`;
2. regular discover-only gates remain green and read-only bootstrap preparation
   returns distinct clean candidate/rollback roots with exact launch contracts;
3. the governed `--promote --bootstrap-mutable-incumbent` transaction is run,
   with automatic rollback on any failed postcheck;
4. post-promotion readback proves one immutable supervisor, three fresh loops,
   equal authoritative hashes, lease parity, provider readiness, and unchanged
   transaction-bound config bytes.

Overall result: **blocked preflight, no live mutation**.
