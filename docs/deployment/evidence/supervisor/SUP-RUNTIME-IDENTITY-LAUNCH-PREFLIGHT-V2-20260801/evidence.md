# SUP-RUNTIME-IDENTITY-LAUNCH-PREFLIGHT-V2-20260801 evidence

Status: `review_pending`

Owner: Codex

Reviewer: Human/Ops

Repository / PR: `ajoe734/pantheon` / replacement PR pending

## Result

The full read-only promotion preflight now composes the two merged V2 identity
layers with a mechanically validated next-launch contract. The snapshot is
eligible only when one immutable candidate, one exact incumbent, the complete
governed launch contract, the existing runtime-health gates, and four candidate
identity revalidations all pass.

The launch contract reads descriptor-bound identities and SHA-256 hashes for
the actual supervisor, watchdog, sync-dev-root, config-provisioning, and
status-command sources. It requires the configured interpreter to exist at a
canonical executable path; binds the full configured argv and exact candidate
cwd device/inode; and pins command root, runtime SHA, remote, `origin/dev`, and
canonical status root.

The final launch environment is assembled by removing worker, Git, legacy
status-runtime, task-state, worktree, Python-overlay, and provider-config
variables, then installing the five exact command/status pins. Snapshot output
contains only those required values plus a hash/count of environment names; it
does not emit full argv or arbitrary environment values.

## Durable runtime paths

The preflight validates, without creating or changing them:

- the authoritative task-state event log and writable parent;
- the isolated writable worker-worktree root, which cannot contain the command
  runtime and cannot live inside it;
- the writable canonical intentional-restart directory and any existing safe
  restart record; and
- one canonical writable log target under
  `<status-root>/.orchestrator/logs/`, shared by stdout and stderr.

All directory and file checks are no-follow and descriptor-bound. Missing,
symlinked, non-regular, non-executable, identity-changing, unsafe, or injected
unwritable targets fail closed.

## Discover-only transaction

`scripts/promote-supervisor-runtime.sh` always invokes the Python module with
`--discover-only`. That path performs the entire candidate, process, launch,
health, canonical-state, and final immutable readback transaction. It does not
signal, launch, stop, restart, roll back, or write config/runtime state.

The end-to-end positive fixture is a clean direct
`command-runtimes/<40-hex-HEAD>` repository with exact remote/dev ancestry,
split status/event/worktree roots, one injected exact process generation, and
all runtime-health inputs. It exits eligible. A realistic linked worktree named
`pantheon-runtime-promotion-review-pr4433` is rejected before procfs discovery,
so the rejected PR #4433 execution shape cannot qualify as a command runtime.

## Immutable revalidation

The candidate config/root/Git identity is revalidated:

1. after initial root and Git discovery;
2. after exact incumbent discovery (in addition to the process layer's
   lock-bracket revalidation);
3. after launch-contract assembly; and
4. after all later health/state reads, immediately before eligibility is
   returned.

A deterministic late config-byte drift at the last stage makes both candidate
and launch invariants fail and the snapshot ineligible.

## Verification

| Command | Result |
|---|---|
| isolated full `scripts/test_promote_supervisor_runtime.py` | 168 passed |
| promotion + runtime-health + sync-dev-root + status-pin + watchdog suite | 244 passed, 14 subtests passed |
| direct `.orchestrator/test_supervisor.py -k worktree` | 21 passed |
| Python compile, shell syntax, wrapper help, and `git diff --check` | passed |
| rejected #4437/#4438 head ancestry checks | all five are not ancestors |

The regression matrix includes interpreter, cwd, required/mismatched/forbidden
environment, source executable, task-state, worktree, intentional-restart,
stdout/stderr, symlink, unwritable injected filesystem, late drift, persistent
runtime, temporary reviewer-worktree, and explicit zero-signal/zero-launch/
zero-write cases. The full merged root/config/Git and process-generation matrix
remains green.

## Deliberate non-scope and next gate

This task changes no config, live process, supervisor/watchdog implementation,
canonical JSON, provider policy, product controller, or service. Rollout is
source merge only. Rollback reverts the three V2 identity merge commits in
reverse order.

This manifest deliberately remains `review_pending`. After the replacement PR
exists, it will be bound to the final exact PR head and PR #4437 will be marked
superseded with links to all three V2 tasks. Independent exact-head Human/Ops
approval and green repository checks are required before merge; no live runtime
promotion is claimed or authorized here.
