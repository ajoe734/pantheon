# SUP-RUNTIME-PROMOTION-FAILURE-MATRIX-INTEGRATION-OPERATOR-V2-20260802 evidence

Status: `review_pending`

Owner: Codex

Reviewer: Human/Ops

Repository / PR: `ajoe734/pantheon` / pending clean integration PR

## Result

The merged snapshot, immutable runtime identity, incumbent process binding,
governed launch preflight, and rollback transaction now form one qualified
promotion contract. This task did not run `--promote`, signal the live
supervisor, edit live or repository config, change provider/account policy, or
modify canonical task JSON.

Final integration found and closed two gaps that isolated predecessor tests did
not exercise:

1. The transaction acquired `runtime-admission.lock` with a private flock
   implementation, then the production watchdog intent writer tried to acquire
   the same inode through `common.stable_sidecar_lock`. Those independent open
   file descriptions could self-deadlock in the same operator process before
   TERM. `RuntimeAdmissionLock` now enters the canonical
   `runtime_admission` lock plane, retains bounded external contention, and is
   process-local re-entrant with the real watchdog writer.
2. Runtime evidence previously required an arbitrary explicit path and could be
   written below the candidate or incumbent command root after postchecks,
   leaving the accepted executable root dirty. Promotion now defaults to the
   git-external runtime evidence directory and rejects either executable root
   before intent or TERM. A rejected explicit path produces durable aborted
   evidence at the safe default path and never creates the requested in-root
   leaf.

## Deleted-cwd incident chain and closure

The superseded PR #4433 path launched a supervisor from a disposable task
worktree. Worker cleanup owns that tree family; the supervisor's own cleanup
scan excludes its own PID and can remove an inactive leased worktree after the
branch lifecycle ends. The process therefore survived with a deleted cwd while
its source path, Git identity, command-runtime pin, and future restart target no
longer formed a durable contract. The old swap helper also had no automatic
post-launch rollback, so Human/Ops had to restore the persistent accepted
runtime manually.

The integrated invariants close each link:

| Surface | Integrated invariant |
|---|---|
| `sync-dev-root.sh` and command-runtime refresh | Candidate must be a direct persistent `/home/lupin/pantheon-ci-deploy/command-runtimes/<40-hex-sha>` root whose exact commit/tree is an accepted `origin/dev` ancestor; a mutable dev-root alias, task worktree, deleted cwd, nested path, or symlink is rejected. |
| Config provisioning boundary | The exact live config path, every path component, inode, byte length, bytes, and SHA-256 are captured and revalidated. Promotion never calls provisioning or drift repair. |
| Watchdog intentional restart | Intent is PID-bound and candidate-commit-bound, written under the same canonical runtime-admission lock plane as the transaction, and verified by a real cross-module re-entry test. |
| Status-command runtime pinning | Launch environment pins command root, SHA, remote, base ref, canonical status root, and the absolute authoritative event log; worker/Git/legacy override variables are scrubbed. |
| Task-worktree cleanup | Command root and worker-worktree root may not contain one another. Exact cwd device/inode plus Git commit/tree prevent a supervisor running from a cleanup-owned task tree. |
| Authoritative projection | Every observation requires authoritative mode, `ok`, `caught_up`, no error, and equal projected/expected hashes. |
| Worker and queue leases | Active worker, event, lease owner, worktree lease, retry lineage, and reverse links must agree; duplicate workers, orphan leases/events, missing history, and lineage cycles fail closed. |
| Provider readiness | Only providers used by active workers/leases are required, but each required provider must be auth-ready and local-worker-ready. Codex and Codex2 account/quota identities remain distinct and unchanged. |
| Process swap and rollback | Exact PID/starttime, argv, executable, cwd, commit, tree, environment, singleton lock owner, and three strictly post-launch monotonic loops are required. Unknown spawned children prohibit a competing rollback launch. |
| Runtime evidence | Default and fallback evidence is outside both executable roots; explicit in-root paths abort before intent/TERM. |

## Deterministic matrix

`failure-matrix.json` binds the successful paths and all required negative
families to concrete test selectors. It covers snapshot/projection, root/Git/
config, process/launch, candidate rollback, rollback failure, temporal loop
ordering, unknown-child containment, lock composition, PID reuse, and evidence
path containment. All transaction tests patch `os.kill`; the two production
termination adapter tests patch it independently and assert exact generation or
no call. No test launches or signals the live supervisor.

## Verification

The following commands ran from this task worktree with inherited live/runtime
bindings removed:

| Command | Exact result |
|---|---|
| `.venv-pantheon/bin/python3 -m pytest -q scripts/test_promote_supervisor_runtime.py` | 204 passed |
| `.venv-pantheon/bin/python3 -m pytest -q scripts/test_promote_supervisor_runtime.py scripts/test_supervisor_runtime_health.py scripts/test_sync_dev_root.py scripts/test_status_command_runtime_pin.py .orchestrator/test_supervisor_watchdog.py scripts/test_dev_supervisor_watchdog_deploy_contract.py` | 281 passed, 14 subtests passed |
| `.venv-pantheon/bin/python3 -m pytest -q scripts/test_ai_status.py` | 157 passed, 31 subtests passed |
| `.venv-pantheon/bin/python3 .orchestrator/test_supervisor.py` | 515 tests passed |
| `.venv-pantheon/bin/python3 -m pytest -q .orchestrator/test_worker_runner_heartbeat.py .orchestrator/rewrite/test_task_state_runtime_env.py` | 28 passed |
| `.venv-pantheon/bin/python3 -m py_compile scripts/promote_supervisor_runtime.py scripts/test_promote_supervisor_runtime.py scripts/supervisor_runtime_health.py` | passed |
| `bash -n scripts/promote-supervisor-runtime.sh scripts/sync-dev-root.sh scripts/run-supervisor-watchdog.sh scripts/ai-status.sh` | passed |
| `scripts/promote-supervisor-runtime.sh --help` plus option check | `--discover-only`, `--promote`, and the optional external-default `--evidence-path` contract present |
| JSON parse, commit-trailer check, and `git diff --check` | passed |

One supplemental legacy wrapper run,
`pytest scripts/test_supervisor.py .orchestrator/test_worker_runner_heartbeat.py
.orchestrator/rewrite/test_task_state_runtime_env.py`, reported 31 passed and 4
failures. The four failures are stale `scripts/test_supervisor.py` expectations
for removed provider-reassignment behavior and
`dispatch_underutilization_sidecars`; both that test file and
`.orchestrator/supervisor.py` are byte-identical to `origin/dev`. They are not
caused by this task, are outside its promotion-only scope, and are not hidden
or counted as a green qualification run. The current core supervisor direct
suite (515), full ai-status suite, runtime binding subset, and required
repository checks are the task gates.

## Delivery, supersession, and review gate

The task branch started at the exact merged rollback dependency
`c92e60ceaff895a6fa5fdd7e39bcd96e9a409bc1`. Its dependency chain is:

- PR #4434 / `cd770e5dca6c13fb1d0679a1bdba9f8934ae80c2` — snapshot invariants;
- PR #4443 / `11d766efbd8bf81aca447d4ccf213109d7263dac` — root/config/Git identity;
- PR #4495 / `ec52d6d687732055b75cd6ae441d3c64f00c3240` — process binding;
- PR #4497 / `79e02ee059387044eec1d21a283e4848f814f49a` — launch preflight;
- PR #4500 / `c92e60ceaff895a6fa5fdd7e39bcd96e9a409bc1` — rollback transaction.

Polluted PR #4433 remains open only until this clean integration PR exists.
The replacement PR must link #4433 as superseded, and #4433 must then be closed
without reusing its head `2905e147573a2ec7741b1c2a9bb792267a22eb9c` or any prior approval.

This manifest remains `review_pending`. Human/Ops must review the final exact
head after every repository check and the root merge-freeze gate are green,
bind this `evidence.json`, and authorize protected merge. This task does not
self-write `review_approved`.

The branch composed `origin/dev` at
`f3dae9017e448591ebbda91a9bf17f4a9c715a66` in merge commit
`08d40a3396598761213165978838f3e3a42dd3d8`. Every qualification command in
the verification table was rerun after that composition and retained the
recorded result.

## Rollout and rollback

Rollout is source merge only. No automatic live promotion follows the merge.
The merged helper is handed to
`SUP-AGENT-ALIAS-GUARD-LIVE-PROMOTION-20260731` as its resolved rollback
contract dependency, and the next already-governed runtime promotion is the
separate Human/Ops canary with gate-before-switch.

Source rollback reverts this integration merge, then PR #4500, #4497, #4495,
and #4443 in reverse order if their layers must also be retired. A live canary
rollback restores the incumbent root/launch contract captured by that canary;
this task performed no live rollout.
