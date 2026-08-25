# Local Development Tooling Runbook

## Scope

Development tooling is local to the repository. It owns engineering tasks,
supervisor dispatch, worker leases, and task-packet materialization. Product
BFF, the hosted frontend, and product deployment do not host or operate this
control plane.

## Local entry points

- `scripts/human-ops-status.sh` and `scripts/ai_status.py` maintain canonical
  tasks.
- `.orchestrator/development_bridge/` verifies and materializes local task
  packets.
- `.orchestrator/assistant-dev-packets/` is the local packet inbox.
- The V2 supervisor drains the inbox and records accepted tasks under
  `ai-task-archive/tasks/`.

Use a clean task worktree and the ordinary branch/PR flow for source changes.
Do not use product BFF routes to generate development documents, create task
packets, prepare a worktree, mutate canonical tasks, or inspect supervisor
state; those routes do not exist.

## Product diagnostics boundary

`POST /bff/management/nl/ask` may provide product conversation and read-only
diagnostics through `kernel_debug`. It does not write source files, create a
development worktree, or dispatch workers. Product BFF health is not evidence
of supervisor health, and supervisor health is not evidence of product
readiness.

## Local packet acceptance

For a local task packet, verify all of the following:

1. The packet is placed in the local pending inbox.
2. The supervisor records a processed receipt.
3. The canonical task record appears under `ai-task-archive/tasks/`.
4. The resulting task is eligible under the V2 dispatch evaluator.

If a task needs a direct Human/Ops change, use the local status command with a
specific task identifier and reason. Do not edit task JSON, queue JSONL, or
runtime state files by hand.

## Functional versus privileged task lanes

Functional, paper, read-only, CI, and reconcile-only packets remain executable
when no hosted/operator-live authorization window exists. They still require
the trusted Ed25519 bridge signature, canonical dependency validation, and
authoritative task-state readback. Their signed `workClass` is one of
`functional`, `paper`, `read_only`, `ci`, or `reconcile_only`.

Only `security`, `hosted`, and `live` packets require the one-shot MFA-backed
`assistant.canonical.mutate` operator authorization. A blocked hosted/write-proof
packet must not prevent the supervisor from dispatching an independent
functional packet.

## Removing development tooling

After product release, archive tasks and verify that no worker, lease, or queue
intent remains. Then disable the supervisor/watchdog and remove
`.orchestrator/`, `ai-task-archive/`, and the local status/packet scripts. The
product image and product deployment are already independent of those paths.
