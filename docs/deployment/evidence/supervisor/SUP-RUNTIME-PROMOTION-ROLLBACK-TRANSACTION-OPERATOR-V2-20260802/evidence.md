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
tree. Candidate and rollback launch return times are captured independently;
each accepted `last_successful_loop_at` must be strictly after its launch
boundary and strictly greater than the prior accepted marker. A marker
regression fails closed instead of allowing later out-of-order values to make
up a three-loop window. Config bytes must remain unchanged and every snapshot
invariant must pass on every accepted observation.

Any launch or postcheck failure enters the governed rollback protocol. If
`Popen` succeeds but procfs generation capture fails, the OS backend uses the
parent-owned child handle for bounded terminate/wait/kill containment. Rollback
may launch the incumbent only after the exact candidate generation was stopped
or the spawned child is proven absent. An unknown still-live child produces
durable `rollback_failed` evidence and prohibits the competing rollback launch.
Otherwise rollback launches the captured incumbent root with its captured
governed argv/environment/log contract and requires a new PID plus three fresh
successful loops. The final rollback observation must match the baseline
projection, worker/queue/lease digest, provider baseline, and config hash.
`rolled_back` and `rollback_failed` both return nonzero.

Durable runtime evidence records the full state history, original and rollback
errors, actual PIDs, both roots/commits/trees/config hashes, and all accepted
loop observations. Runtime evidence writes are atomic, directory-fsynced, and
reject symlink/non-regular evidence leaves.

## Failure matrix

Mock-only tests inject candidate launch failure, missing heartbeat, wrong cwd,
wrong commit, wrong tree, projection mismatch, lease mismatch, duplicate worker,
provider-not-ready, config drift, rollback launch failure, rollback config
drift, rollback projection/worker/provider baseline drift, pre-TERM snapshot
drift, same-owner lock reentry, and candidate/rollback loop markers that are
stale, equal to the launch boundary, regressing, or out of order. OS launch
tests mock `Popen` and procfs to prove both successful exact-child containment
and an uncontained still-live unknown child; transaction evidence then proves
the latter never starts rollback. Production termination adapter tests patch
`os.kill` and prove exact-generation signalling plus PID-reuse rejection. No
test signals a live PID or launches a live supervisor.

## Verification

| Command | Result |
|---|---|
| isolated `scripts/test_promote_supervisor_runtime.py` | 199 passed |
| promotion + runtime-health + sync-dev-root + status-pin + watchdog suite | 275 passed, 14 subtests passed |
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
not reused. Human/Ops then rejected rebuilt head
`aa12fb23d9cea8ea7baccc31fb9f08779f36837e` because loop markers lacked launch
boundaries/order enforcement and a post-spawn generation-capture failure could
permit rollback without proving the candidate absent. Remediation anchor
`31a94665d9b3e124fa5b5891dc111815903cfa79` and its follow-up implement those
two requested safety changes. Human/Ops must review the new final exact head.
