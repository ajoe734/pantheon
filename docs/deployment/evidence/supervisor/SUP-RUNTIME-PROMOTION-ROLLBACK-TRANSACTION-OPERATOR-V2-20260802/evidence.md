# SUP-RUNTIME-PROMOTION-ROLLBACK-TRANSACTION-OPERATOR-V2-20260802 evidence

Status: `review_pending`

Owner: Codex

Reviewer: Human/Ops

Repository / PR: `ajoe734/pantheon` / `#4500`

## Result

The supervisor promotion command now has an explicit, fail-closed transaction
mode in addition to its default read-only discovery mode. The transaction
composes the merged immutable root/config/Git identity, exact process-generation
binding, snapshot invariants, and governed launch contract. No live promotion
was run for this task.

The operator holds the canonical `runtime-admission.lock` while it revalidates
the exact incumbent process, config bytes, state/status/provider documents, and
candidate/rollback launch contracts. The lock object is same-owner reentrant,
so rollback can follow the same admission protocol without acquiring a second
flock or self-deadlocking. Intent is durably recorded through the watchdog
contract before TERM, and TERM/KILL may address only a captured PID/starttime
generation. A reused PID is never signalled.

## Acceptance and rollback

Candidate acceptance binds the exact launched PID/starttime, root, commit, and
tree. It requires three separately observed `last_successful_loop_at` values
after the incumbent baseline, unchanged config bytes, and every snapshot
invariant to pass on every accepted observation.

Any launch or postcheck failure enters rollback. Rollback stops only the
captured candidate generation (or the still-verifiable incumbent when its first
termination was incomplete), launches the captured incumbent root with its
captured governed argv/environment/log contract, and requires a new PID plus
three fresh successful loops. The final rollback observation must match the
baseline projection, worker/queue/lease digest, provider baseline, and config
hash. `rolled_back` and `rollback_failed` both return nonzero.

Durable runtime evidence records the full state history, original and rollback
errors, actual PIDs, both roots/commits/trees/config hashes, and all accepted
loop observations. Runtime evidence writes are atomic, directory-fsynced, and
reject symlink/non-regular evidence leaves.

## Failure matrix

Mock-only tests inject candidate launch failure, missing heartbeat, wrong cwd,
wrong commit, wrong tree, projection mismatch, lease mismatch, duplicate worker,
provider-not-ready, config drift, rollback launch failure, rollback config
drift, rollback projection/worker/provider baseline drift, pre-TERM snapshot
drift, and same-owner lock reentry. Production termination adapter tests patch
`os.kill` and prove exact-generation signalling plus PID-reuse rejection. No
test signals a live PID or launches a live supervisor.

## Verification

| Command | Result |
|---|---|
| isolated `scripts/test_promote_supervisor_runtime.py` | 188 passed |
| promotion + runtime-health + sync-dev-root + status-pin + watchdog suite | 264 passed, 14 subtests passed |
| direct `.orchestrator/test_supervisor.py -k worktree` | 21 passed |
| Python compile, shell syntax, CLI help, and `git diff --check` | passed |

## Deliberate non-scope and next gate

This task does not edit live or repository supervisor config, canonical JSON,
provider/account/quota policy, product controllers, supervisor/watchdog source,
or services. It does not equate Codex and Codex2 accounts or change global
review policy. Rollout is source merge only; source rollback is revert of this
task's eventual merge commit.

PR #4500 is open with auto-merge disabled. Independent Human/Ops review must
bind the final PR head after this PR-binding commit and all repository checks
pass. This manifest remains `review_pending` until that review occurs.

Human/Ops reopened the prior head `770dbdafcb3b9ffa966046fe19ce357535acac5c`
because three task commit subjects exceeded the 72-character repository limit.
The task-owned history was rebuilt from current `origin/dev` without changing
the implementation tree. The rejected head and any earlier review binding are
not reused; Human/Ops must review the new exact head.
