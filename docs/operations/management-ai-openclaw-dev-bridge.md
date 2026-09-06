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

`security`, `hosted`, and `live` packets no longer require an operator
authorization at intake (OPS-PRIVILEGED-TASK-EXECUTION-AUTH-001 retired that
former MFA-at-intake rule). A correctly signed privileged packet materializes
the same as a functional one, but the resulting canonical task carries an
immutable, non-executable `execution_authorization` pending-authorization
hold (`task["execution_authorization"]["state"] == "pending_authorization"`)
instead of becoming dispatchable. `scripts/ai-status.sh show <task-id>`
surfaces that redacted state directly. A blocked/pending privileged task must
not prevent the supervisor from dispatching an independent functional
packet — see the "Execution authorization" section below.

## Execution authorization (privileged task execution, separate from intake)

Genuine MFA is required later, separately, at actual execution — never at
intake. `.orchestrator/execution_authorization.py` is the sole module that
derives a privileged task's immutable execution policy, verifies an
independently issued MFA-bound grant against it, and enforces one-shot
consumption so a grant authorizes exactly one dispatch attempt. It is fed
into the existing shared planner/delivery predicate
(`rewrite/dispatch_admission.py`'s `TaskIntent.execution_authorized`) and
spent at the actual claim/lease boundary
(`supervisor.reserve_execution_authorization_for_launch`, called immediately
before the adapter process launches).

Human/Ops CLI:

```bash
AI_NAME=Human/Ops \
EXECUTION_GRANT_JSON="$(cat grant.json)" \
EXECUTION_MFA_ISSUER_PUBLIC_KEYS_JSON='{"<issuer-key-id>":"<base64url-public-key>"}' \
PANTHEON_LOCAL_HUMAN_OPS=1 \
./scripts/ai-status.sh execution-grant-submit <task-id>

AI_NAME=Human/Ops \
PANTHEON_LOCAL_HUMAN_OPS=1 \
./scripts/ai-status.sh execution-grant-revoke <task-id> "<reason>"
```

`EXECUTION_MFA_ISSUER_PUBLIC_KEYS_JSON` is a trust root kept distinct from
`BRIDGE_SIGNING_PUBLIC_KEYS_JSON`: a dev-bridge packet-source key is never an
accepted MFA issuer. See
`docs/04/pantheon_first_release_closure_2026-09-06/EXECUTION_AUTHORIZATION_SA_SD.md`
for the full grant field contract and the approved plan this implements.

## Removing development tooling

After product release, archive tasks and verify that no worker, lease, or queue
intent remains. Then disable the supervisor/watchdog and remove
`.orchestrator/`, `ai-task-archive/`, and the local status/packet scripts. The
product image and product deployment are already independent of those paths.
