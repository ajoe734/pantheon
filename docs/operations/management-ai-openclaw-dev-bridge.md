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
4. Canonical readback distinguishes ordinary eligibility from
   `admitted_pending_authorization`; a privileged pending receipt is accepted
   intake and carries no execution permission.

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

Genuine MFA is required at execution. The immutable approved plan is
[EXECUTION_AUTHORIZATION_SA_SD.md](../04/pantheon_first_release_closure_2026-09-06/EXECUTION_AUTHORIZATION_SA_SD.md),
byte-identical to `/tmp/pantheon-execution-auth-20260906.2Y96ee/SA_SD.md`
(SHA256 `dde7dfc27ca02bf5d8920c9e176d2d543904540a5103cf0c756b0d7b73372e66`).
Its original assignment is historical; canonical assignment currently names
Codex2 as implementation owner and Codex as independent reviewer.

`execution_authorization.py` binds the verified full signed task spec and its
hash, work class, repository, environment, resources and action into one policy
digest. Current contract, task generation and assignment must still match at
grant submission, reservation and worker entry. Scope changes invalidate the
grant; they cannot silently rewrite the immutable policy. A new signed
contract is required for changed source obligations.

The existing shared planner and late-delivery predicate denies privileged
owner execution before capacity/worktree/provider launch. The supervisor
reserves one grant under the canonical TaskStore lock for one event attempt,
then checks it again under the lock held through adapter launch. A crash after
reservation spends that attempt; replay cannot recover authority for another
attempt. `worker_runner.py` waits for the existing durable worker/launch receipt
and verifies its PID/start ticks, command, workspace, task, generation and
canonical role. That receipt binds the authoritative journal; caller-selected
command text, role labels and `ai-status.json` mirrors confer no authority.

Revocation, reopen and reassignment restore the existing `waiting_for=Human/Ops`
pending hold. Active execution rechecks its bounded reservation on the existing
heartbeat and uses the bounded process termination path when authority is lost.
Termination is not a rollback receipt; any compensation remains owned by the
existing hosted protocol and environment lease. Read-only review/finalization
cannot spend a mutation grant or clear a pending hold.

Use the qualified command runtime supplied by the supervisor. A worker reads
redacted state using its actual identity, for example:

```bash
AI_NAME=Codex2 "$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" show <task-id>
```

Human/Ops submits an independently issued assertion through the existing local
operator ingress (the example reads an already-issued grant; it creates no
credentials):

```bash
EXECUTION_GRANT_JSON="$(cat grant.json)" \
  "$PANTHEON_COMMAND_ROOT/scripts/human-ops-status.sh" execution-grant-submit <task-id>
"$PANTHEON_COMMAND_ROOT/scripts/human-ops-status.sh" execution-grant-revoke <task-id> "<reason>"
```

The assertion is canonical JSON signed with Ed25519, with `signature.algorithm`,
`signature.key_id`, and base64url `signature.value`. Required body fields are
`task_id`, `generation`, `policy_digest`, `repository`, `environment`,
`resources`, `action_scope`, `purpose=pantheon.execution.mfa`,
`capability=assistant.canonical.execute`, `audience=<task-id>`,
`mfa_verified=true`, `mfa_actor`, `nonce`, `issued_at`, and `expires_at`.
The issuer, not the submitter, vouches for MFA. Start validity is at most 300
seconds; `run_ttl_seconds` separately bounds the consumed run (default 3600,
maximum 86400). A nonce cannot be submitted twice. Persisted task state keeps
scoped bindings and redacted references, not the bearer assertion or a key.

Trusted public issuers are configured at
`execution_authorization.mfa_issuer_public_keys` in the qualified runtime's
`.orchestrator/config.json`, independently of bridge source signing trust and
the submitting environment. An empty issuer set closes grant submission with
an actionable reason while signed pending intake remains available.

Source acceptance, qualified runtime acceptance, pending intake and hosted
acceptance are separate receipts. Runtime promotion must use the existing
`promote_supervisor_runtime.py` discover-only preflight with explicit roots and
candidate interpreter; its isolated behavioral probe requires both intake and
late execution barriers and records imported candidate paths. An unsafe older
runtime cannot be promoted or used for rollback. Only after accepted source,
qualified runtime and no-MFA/no-launch proof may the coordinator submit the
original `DEV-RELEASE-HOSTED-001`, `L12-HOSTED-001` and `MGMT-AGORA-E2E-001`
as pending, preserving IDs and contracts and rechecking deduplication. This
source task issues no live grant and performs no hosted operation.

## Removing development tooling

After product release, archive tasks and verify that no worker, lease, or queue
intent remains. Then disable the supervisor/watchdog and remove
`.orchestrator/`, `ai-task-archive/`, and the local status/packet scripts. The
product image and product deployment are already independent of those paths.
