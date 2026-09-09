# Worker recovery: one authority, preserved task source

Date: 2026-09-09. Scope: development tooling only.

## Problem and decision

Lost-lease recovery already has one canonical TaskStore receipt and generation
fence. The structural defect was in its workspace handoff: the first recovery
could inherit dirty WIP through a tree-guard exception, while a later recovery
archived that worktree and replaced the task branch with the current dev base.
The latter preserved a Git ref but removed the task's committed implementation
from the replacement worker's starting point. Operational recovery messages
also replaced task instructions, losing source/rejection context.

Use the existing recovery receipt as the sole authority. Preserve the committed
task branch; quarantine uncommitted WIP before dispatch; carry source identity
and unresolved instructions as advisory context. Do not add a second recovery
state machine, queue, journal, product API, compatibility endpoint, or daemon.

## Ownership and sequence

1. The supervisor detects a lost process/lease and atomically fences its task
   generation in the existing canonical receipt. Before transition cleanup it
   captures flat continuation facts under `receipt.previous.continuation`.
2. Existing reassignment/reservation selects the replacement. Filesystem
   admission validates the current receipt, generation, lifecycle role, actor,
   queue intent, repository, task branch, registered lease and worker liveness.
3. For dirty WIP, the filesystem owner preserves the exact committed HEAD in a
   private recovery ref and builds a complete, checksummed, durable archive.
4. The supervisor records the archive binding at `receipt.workspace` in the
   existing TaskStore transaction and activity outbox. Conflicting or obsolete
   receipt publication fails closed, leaving source WIP untouched.
5. Only after publication does the filesystem owner restore tracked/indexed
   files to that same task HEAD and remove individually verified archived
   untracked files. It never resets the branch to dev or removes ignored files.
6. Ordinary safe reuse/fast-forward and the ordinary tree guard run. Diverged
   task commits remain for the worker to reconcile; Git/status failures block.
   The request includes canonical continuation and current quarantine provenance.

`worker_recovery.py` owns typed receipt facts/context projection;
`worker_workspace.py` owns filesystem preservation/admission;
`supervisor.py` owns canonical transactions and dispatch. Filesystem archive
metadata is evidence, not another task-state authority.

## Receipt and context contract

`receipt.workspace` contains exactly these allowlisted facts:

| Field | Meaning |
| --- | --- |
| `repository_id` | Canonical delivery repository |
| `workspace_path` | Registered isolated task worktree |
| `branch` | Canonical task branch |
| `source_head` | Exact committed HEAD before WIP cleanup |
| `archive_path` | Complete local archive directory |
| `preserved_branch_ref` | Private Git ref preserving that HEAD |

Repeated publication of the same binding is idempotent; another binding cannot
overwrite it. A retry after publication reuses the exact archive/ref and checks
remaining source against it. Changed WIP, missing/corrupt archive data,
unsupported paths, read failures, or incomplete/oversized archives block cleanup.
Partial archives remain for inspection and are not permission to remove source.

Continuation carries prior PR/head/repository identity, current `task.next`,
unresolved reviewer requirements and earlier quarantine provenance. It excludes
approval bindings/proofs, execution grants and nonces. Current task instructions
win; a new owner/Human-Ops reopen starts a new rejection context. An obsolete
role, pointer, actor or generation cannot project replacement context.

## Retired implementations and retained boundaries

| Retired implementation | Single replacement |
| --- | --- |
| Direct dirty-WIP adoption flag and tree-guard exception | Canonically qualified quarantine, then ordinary tree guard |
| Stale-adoption detector and recovery reset-to-base helper | Preserve task HEAD on every recovery; one archive path |
| Adoption/replacement-specific lease fields | Projection of canonical `receipt.workspace` |
| Separate unused porcelain path parser | Existing NUL-delimited dirty-entry parser for archive paths |
| Recovery status text overwriting `task.next` | Operational receipt/events separate from task instructions |
| Legacy brief removal based on a text marker | Ordinary dirty-source admission; qualified recovery archives WIP |
| Separate reused/recreated branch refresh admission blocks | One shared refresh and failure policy |

Ordinary regenerable scratch cleanup, index-only split repair, incomplete
checkout-directory quarantine, and terminal/orphan worktree retention have
different preconditions and purposes; they are not alternate lost-lease state
machines. They remain under the same filesystem owner. The shared archive helper
now returns success only for a complete archive, including for retention callers.
Historical evidence documenting retired behavior is retained as history, not as
a live contract.

## Operating and acceptance checks

Before restoring any WIP, inspect the canonical binding, archive manifest,
preserved ref, staged binary patch, unstaged binary patch and archived files.
Use an isolated branch/worktree based on the preserved head for investigation.
Reapply staged and unstaged layers in order and selectively bring verified
changes into the current task branch. Do not blanket-copy an archive over a live
worker, treat archived code as approved, or delete recovery evidence merely
because dispatch resumed.

The regression suites exercise real temporary Git repositories and canonical
TaskStore transactions: ahead/diverged source, staged/unstaged/untracked WIP,
exact restoration, failed publication, interrupted cleanup/retry, corruption,
permissions, role/actor/generation fences, current instructions and rejection
epochs. Tests must not depend on GitHub fetches or mutate live task state.

Relevant commands from a clean task worktree:

```sh
python -m pytest .orchestrator/rewrite/test_worker_workspace.py .orchestrator/rewrite/test_worker_recovery.py .orchestrator/test_supervisor.py -q
git diff --check
```

Tooling source delivery requires local validation, scoped commit, current-dev
integration/push and required branch checks. If GitHub requires a PR transport,
use `delivery:tooling` without product reviewer attestation. Live repair is not
proven until the supervisor runs the delivered immutable source and health checks
pass. Tooling health does not prove Management, Agora, the frontend, or the full
product task backlog is complete.
