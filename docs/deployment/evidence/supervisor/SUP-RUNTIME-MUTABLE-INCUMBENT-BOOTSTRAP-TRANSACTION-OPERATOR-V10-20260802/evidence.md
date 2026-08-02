# SUP-RUNTIME-MUTABLE-INCUMBENT-BOOTSTRAP-TRANSACTION-OPERATOR-V10-20260802

Status: `review_pending`

Owner: Codex2
Reviewer: Human/Ops

## Result

The source repair is ready for independent exact-head review. It does not run a
live promotion, write the live config, signal PID `3538768`, launch a candidate,
or launch rollback.

PR #4522 proved that the current mutable `dev-root` incumbent cannot satisfy the
existing immutable-incumbent preflight. This repair keeps normal discovery
read-only and keeps normal `--promote` fail-closed for that case. The one-time
bootstrap requires the explicit pair:

```text
--promote --bootstrap-mutable-incumbent
```

## Atomic bootstrap contract

Before any external config mutation, the operator captures the exact mutable
process generation, executable, full argv, cwd device/inode, Git HEAD/tree,
accepted dev tip, remote, live-config identity/bytes/SHA, governed source bytes,
environment contract, and singleton-lock owner generation. It then materializes
that exact accepted incumbent commit under the persistent
`command-runtimes/<40-hex-commit>` root and independently revalidates it as the
rollback runtime.

Candidate and rollback watchdog commands are rendered from the same captured
config. A dedicated promotion lock serializes preparation and the entire
transaction. The shared runtime-admission lock excludes the watchdog launch
authority while the operator:

1. revalidates the captured process/config/state snapshot;
2. records the PID-bound intentional restart;
3. terminates only the captured PID generation and proves it stopped;
4. CAS-checks, atomically replaces, read-backs, and fsyncs the candidate config;
5. launches and captures the candidate generation before releasing admission.

Rollback reacquires admission while the promotion lock is still held, refuses a
competing launch if a spawned child has unknown live identity, installs and
fsyncs the immutable rollback config, and launches only the captured rollback
generation. Candidate and rollback acceptance still require three distinct,
strictly post-launch successful loops; rollback also requires projection,
worker/queue/lease, and provider baseline parity.

## Failure matrix

`failure-matrix.json` maps every negative family to concrete selectors. New
coverage includes explicit opt-in, mutable PID reuse and ambiguity, tracked or
unaccepted source identity, immutable rollback materialization, promotion lock
contention and symlink rejection, config replacement races, every instrumented
config-write crash window, candidate config failure, rollback config failure,
directory fsync failure, and unknown-child containment. Existing root, Git,
gitlink, symlink/path swap, process, launch, temporal-loop, lease, provider, and
rollback matrices remain green.

## Verification

- Promotion, health, sync, watchdog, and deploy-contract matrix: `295 passed`.
- AI status and core supervisor matrix: `744 passed, 189 subtests passed`.
- Python compilation, shell syntax, CLI option check, and `git diff --check`:
  passed.

One supplemental integration command included
`scripts/test_status_command_runtime_pin.py` and reported `300 passed`, `14`
subtests passed, and two failures. Both failing pre-existing fixtures omit the
exact process generation now required by `ai_status.py`. Neither
`scripts/ai_status.py` nor that test file differs from this task's branch base;
the failures are recorded rather than hidden or counted as green qualification.

## Governance and next action

No canonical task JSON, queue or active-lease policy, provider readiness,
Codex/Codex2 account or quota grouping, global reviewer rule, watchdog source,
or supervisor scheduling source changed. Human/Ops remains the explicit
reviewer.

After required checks and Human/Ops exact-head review, merge this source task to
`dev`. Only then should the blocked V9 canary be redispatched with a newly
selected candidate containing the merge. That separate canary must perform the
real transactional rollout and rollback proof; this source task claims none of
that live evidence.
