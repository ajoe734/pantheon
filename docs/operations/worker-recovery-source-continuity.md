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

Preparation revalidates an existing archive even when cleanup already left a
clean worktree. If ordinary refresh has since fast-forwarded the branch, the
same validator accepts only a clean descendant of the archived source HEAD;
the historical receipt/ref/archive are unchanged. A dirty descendant, unrelated
HEAD, failed ancestry query, or missing/corrupt archive blocks the handoff.
Git ancestry exit code 1 means a negative relationship; other nonzero exits
are errors, never permission to dispatch a supposedly diverged worktree.

Continuation carries prior PR/head/repository identity, current `task.next`,
unresolved reviewer requirements and earlier quarantine provenance. It excludes
approval bindings/proofs, execution grants and nonces. Current task instructions
win; a new owner/Human-Ops reopen starts a new rejection context. An obsolete
role, pointer, actor or generation cannot project replacement context.

Resolving an obsolete receipt after responsibility moves to another lane also
preserves the successor's exact `task.next` (including an absent field). Its
operational message belongs only to the existing resolved activity event;
replaying resolution does not rewrite instructions or emit another event.

Receipt retention protects the current canonical pointer of every nonterminal
task, including materialized, held and resolved receipts: ending assignment
fencing does not end the need for source continuation. Terminal tasks, orphaned
receipts and mismatched task/receipt identities are prunable. The 128-receipt
history target is a soft limit when live references alone exceed it; retention
never drops live context to meet that limit or retains entire previous chains.

Supervisor and Human/Ops import the same recovery-fence predicate from
`rewrite/worker_recovery.py`. It checks lifecycle before inspecting only the
relevant generation: pending uses the fence epoch, reassigned uses the
replacement epoch. Missing or non-positive/non-integer active epochs fail
closed; valid stale epochs do not fence a newer task. Resolved/held/materialized
history cannot reactivate fencing because an unrelated old field is malformed.
This pointer check is not a substitute for full transition receipt validation.

Review-intent CAS hashing has one existing owner,
`rewrite/task_state_store.py:review_decision_task_digest`; supervisor and CLI
import it directly. The exact-actor/nonce review-intent replay and generic
worker reassignment are mutually exclusive lifecycles, not competing recovery
implementations. Review replay must not acquire generic generation fencing.

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
| CLI copy of the active recovery-fence predicate | Direct import of the canonical worker-recovery predicate |
| Supervisor and CLI copies of review task digest/exclusion rules | Direct imports of the existing TaskStore digest |
| Uncalled runner role/revalidation shortcuts and unused recovery locals | Existing entry/running binding validation; preserve adoption side effects |
| Uncalled supervisor dashboard refresh helper | Existing canonical projection pipeline; no replacement helper |
| Status-root bridge source fallback | Current command-runtime package and module, checked before import/reuse |

Ordinary regenerable scratch cleanup, index-only split repair, incomplete
checkout-directory quarantine, and terminal/orphan worktree retention have
different preconditions and purposes; they are not alternate lost-lease state
machines. They remain under the same filesystem owner. The shared archive helper
now returns success only for a complete archive, including for retention callers.
Historical evidence documenting retired behavior is retained as history, not as
a live contract.

The local bridge reads packet/task data from the configured status root, but
loads executable code only from the supervisor's immutable command root.
Missing command source or a cached module from another root is unavailable,
not a reason to fall back to mutable working-copy code. No product BFF ingress,
credential mechanism, dynamic source loader, or second task authority is added.

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
