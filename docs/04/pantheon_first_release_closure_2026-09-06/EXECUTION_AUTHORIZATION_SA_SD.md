# Execution Authorization SA/SD — repository-readable copy

Status: immutable, byte-identical copy of the operator-approved plan that
task `OPS-PRIVILEGED-TASK-EXECUTION-AUTH-001` implements. This file does not
add, remove, or reinterpret any requirement from the source below; it exists
only so the plan has a durable, committed, provenance-tracked repository
location instead of living solely on an operator workstation or in `/tmp`.

## Provenance

- Source path (operator workstation, `/tmp`): `/tmp/pantheon-execution-auth-20260906.2Y96ee/SA_SD.md`
- `sha256`: `dde7dfc27ca02bf5d8920c9e176d2d543904540a5103cf0c756b0d7b73372e66`
- Referenced by the signed dev-bridge packet: `packet_id=pkt-privileged-execution-auth-20260906-v2`,
  `packet_digest=325967d8df0c347544f5c23eb5749e377e1fe1f6382d51f79e47340f4a52fe18`
  (see `task["dev_bridge"]` on canonical task `OPS-PRIVILEGED-TASK-EXECUTION-AUTH-001`).
- Baseline this plan re-pins at implementation: protected `pantheon/dev`
  `55cd327b9200648e5d42360907dedc17ddf6f5fc`; qualified supervisor runtime
  `dd3f0563a6a3f9ca2976a354de29221d91665a73`.
- The three ORIGINAL hosted task IDs this plan's section 7 refers to
  (`DEV-RELEASE-HOSTED-001`, `L12-HOSTED-001`, `MGMT-AGORA-E2E-001`) remain
  entirely unaccepted; nothing in this repository copy, or in the source
  delivery of this task, submits, re-signs, or admits them. That remains a
  separate, later, explicit step gated on accepted source AND qualified
  runtime/no-MFA no-launch evidence (plan section 7).

## Implementation status of this copy

Delivered by task `OPS-PRIVILEGED-TASK-EXECUTION-AUTH-001`:

- `.orchestrator/execution_authorization.py` — the execution-time
  MFA-bound authorization module (policy derivation over the task's current
  target/resources/artifacts, pending-authorization hold, genuine grant
  verification, one-shot reserve/consume, `reservation_is_current` for the
  direct worker-entry check, revocation). Privileged classification is
  derived from the task's durable `dev_bridge.work_class`, not from whether
  the subrecord happens to be present, so a dropped/downgraded subrecord on
  a privileged task fails closed.
- `.orchestrator/rewrite/dispatch_admission.py` and
  `.orchestrator/dispatch_policy.py` — the one normalized authorization
  verdict wired into the existing shared planner/delivery predicate
  (`TaskIntent.execution_authorized`, `DispatchBlockReason.EXECUTION_AUTHORIZATION_REQUIRED`),
  scoped to the `OWNED_IN_PROGRESS`/`OWNED_READY` owner-execution dispatch
  purposes only; read-only `REVIEW_READY`/`OWNED_FINALIZE` dispatch never
  acquires or clears the grant.
- `.orchestrator/development_bridge/dev_bridge_materialize.py` — the former
  MFA-at-intake gate is retired; a signed privileged (`security`/`hosted`/
  `live`) packet now materializes without an operator grant.
- `scripts/ai_status.py` — `command_assign` attaches the immutable pending-
  authorization hold, and an old-runtime-recognized `waiting_for` hold, at
  materialization time for a privileged dev-bridge task; new
  `execution-grant-submit` / `execution-grant-revoke` Human/Ops CLI commands
  extend the existing local operator status CLI (plan section 3), sourcing
  the MFA-issuer trust root from `.orchestrator/config.json`
  (`execution_authorization.mfa_issuer_public_keys`), never from the grant
  submitter's own environment; `execution-grant-submit` releases the
  `waiting_for` hold once a genuine grant is bound; `show <task-id>` already
  surfaces the redacted authorization state.
- `.orchestrator/supervisor.py` — `reserve_execution_authorization_for_launch`
  is the authoritative claim/lease-boundary one-shot spend, called
  immediately before the adapter process is launched, scoped to
  owner-execution dispatch only (plan section 4).
- `.orchestrator/worker_runner.py` —
  `ensure_execution_authorized_before_launch` independently revalidates the
  exact `STATE_RESERVED` binding (via `ORCH_EXECUTION_AUTHORIZATION_RUN_ID`)
  at actual process-launch time, so a direct invocation that bypasses the
  supervisor's reserve step cannot launch owner-execution work either.
- `scripts/promote_supervisor_runtime.py` —
  `verify_execution_authorization_barriers` discover-only-probes a
  candidate command runtime for both barriers before promotion/rollback,
  refusing an old-runtime target that predates `execution_authorization.py`
  entirely (plan section 6).

Not delivered by this task, and explicitly out of scope per plan section 7:
any live MFA issuer operational setup, and the revised signed submission of
the three original hosted task IDs. Those remain later, separate,
operator-authorized steps.

## Operational command/receipt contract

The trusted MFA-issuer public-key set is configured at
`execution_authorization.mfa_issuer_public_keys` in
`.orchestrator/config.json` — an independently provisioned file, not an
environment variable the same command invocation could also set. An
isolated exact-head review (2026-09-06, Codex2) found that an earlier
revision instead read this trust root from a caller-supplied
`EXECUTION_MFA_ISSUER_PUBLIC_KEYS_JSON` environment variable, which let a
single command invocation supply both a self-generated "issuer" key and a
grant signed by the matching private key. That defect is fixed: the
grant-submit command no longer reads any such environment variable for the
trust root at all.

```bash
# Human/Ops submits one independently verified, signed execution grant.
# The trust root comes from .orchestrator/config.json, never from this
# invocation's own environment.
AI_NAME=Human/Ops \
EXECUTION_GRANT_JSON="$(cat grant.json)" \
PANTHEON_LOCAL_HUMAN_OPS=1 \
./scripts/ai-status.sh execution-grant-submit <task-id>

# Human/Ops revokes a grant, stopping new unauthorized effects.
AI_NAME=Human/Ops \
PANTHEON_LOCAL_HUMAN_OPS=1 \
./scripts/ai-status.sh execution-grant-revoke <task-id> "<reason>"

# Redacted status readback (no secret material is ever stored):
./scripts/ai-status.sh show <task-id>
```

`grant.json` is a canonical-JSON, Ed25519-signed object binding: exact
`task_id`, current assignment `generation`, the task's immutable
`policy_digest`, `repository`, `environment`, `resources`, `action_scope`,
`purpose=pantheon.execution.mfa`, `capability=assistant.canonical.execute`,
`audience=<task_id>`, `mfa_verified=true`, an independently verified
`mfa_actor` identity, `issued_at`/`expires_at` (start-freshness window
&le;300s), a bounded `run_ttl_seconds`, and a one-shot `nonce`. The signing
key must belong to the configured
`execution_authorization.mfa_issuer_public_keys` trust root — kept distinct
from the dev-bridge packet-source keys (`BRIDGE_SIGNING_PUBLIC_KEYS_JSON`)
and from the grant submitter's own environment, so neither a packet-source
key nor a self-supplied trust root can ever double as an MFA issuer. An
empty (default) `mfa_issuer_public_keys` means no genuine MFA issuer is yet
provisioned in this checkout: grant submission fails closed with an
actionable reason while pending intake remains fully usable.

---

## Signed source (verbatim, byte-identical)

Task: OPS-PRIVILEGED-TASK-EXECUTION-AUTH-001. Implementation: existing supervisor/auto-worker, Claude; independent exact-head review: Codex2. Baseline: protected pantheon/dev 55cd327b9200648e5d42360907dedc17ddf6f5fc; current qualified supervisor runtime dd3f0563a6a3f9ca2976a354de29221d91665a73. Re-pin current dev at implementation/delivery. Keep dirty shared-root changes and existing workers untouched.

2026-09-06. Operator explicitly approved: admit privileged tasks as non-executable pending-authorization records first; require genuine MFA before real execution. This supersedes only the former MFA-at-intake rule. It does not grant any MFA assertion, hosted action, product approval, real-capital operation or development-workflow bypass.

### 1. Verified structural gap and single responsibility

Existing development_bridge/dev_bridge_materialize.py rejects privileged admission without BridgeOperatorAuthorization (including mfa_verified) but does not establish a separate execution gate. dispatch_policy.evaluate_task_delivery_admission builds TaskIntent from lifecycle, dependency, human hold, review binding and execution resources; rewrite/dispatch_admission.py is already the sole shared planner and queue-delivery predicate. Neither currently consumes a genuine task-bound execution authorization. Moving only the intake check would therefore silently enable execution.

Retain ONE signed local intake, ONE canonical V2 TaskStore, ONE shared execution-admission predicate and existing runtime/lease/dispatch boundaries. Do not add a queue, scheduler, cron, BFF development route, generic auth framework, approval replica or second execution-policy ladder. permission_broker tool approvals and configured worker/provider login are not human MFA proofs. Product business approval/auth/MFA and cross-repo hosted environment lease remain separate, unchanged requirements.

### 2. Intake invariant

A correctly signed functional/paper/read_only/ci/reconcile_only task keeps existing ordinary execution behavior. A correctly signed security/hosted/live task may be materialized without an operator grant, but must atomically become a canonical non-executable pending-authorization record. Never relabel privileged work functional.

Derive an immutable execution policy from verified packet work class and exact task contract, repository/environment/resources and allowed scope. An omitted, malformed, contradictory or downgraded policy on a privileged record fails closed. Canonical assignment, task metadata, reopen, recovery and replay cannot erase or weaken this policy. Source signatures and idempotent packet admission remain mandatory.

Use the existing lifecycle and a small typed authorization subrecord/hold, not another task lifecycle system. Current status/explain output must distinguish admitted_pending_authorization from runnable, running, reviewed, merged and hosted-accepted. Plain local task maintenance does not require product login.

New pending records must remain non-runnable to older workers/runtimes too: preserve a durable old-runtime-recognized hold and enforce safe runtime/rollback admission. Do not enable deferred-intake packets while the live runtime lacks the final execution guard. Old embedded packet operatorAuthorization or old queued success is not a perpetual or implicit execution grant.

### 3. Genuine grant, no source-signer self-approval

Extend existing local Human/Ops CLI and existing bridge assertion/signature primitives for status, verified grant submission and revocation. Do not issue credentials or expose a product BFF route. A grant is an independently verified operator/MFA assertion with explicit execution purpose/audience, not an untrusted mfaVerified Boolean, source-packet signature, environment flag, chat approval, provider login, permission-broker click or claimed operator ID.

Reuse existing Ed25519/key-verification primitives and configured trust infrastructure with explicit MFA-issuer purpose separation; packet-source keys are NOT automatically authorized MFA issuers. No hardcoded real keys or new signing service. If no genuine MFA issuer/proof is available, grant submission and execution stay closed with an actionable issuer/proof-unavailable reason while intake remains usable. Tests may use explicitly isolated synthetic issuer keys; those are never live proof.

Bind the verified grant to exact task ID and current generation, immutable policy/spec digest, target repository/environment/resources, permitted action/scope, verified operator/MFA identity and freshness, issued/expiry times and one-shot nonce. Define start-freshness separately from the bounded lifetime of an already authorized run; do not grant indefinitely renewable blanket authority. Reassignment/scope revision or a changed target invalidates the grant. Persist only redacted audit references/digests and scoped authorization state in TaskStore, not bearer tokens/private keys or credential hashes.

### 4. Execution barrier and crash safety

Feed the same normalized authorization verdict into the existing shared pure dispatch predicate for planner and late delivery. Deny privileged owner execution before capacity/worktree/provider launch/hosted mutation when authorization is absent, invalid, stale, expired, revoked, consumed by a different attempt, or bound to another task/generation/policy/environment. Add explicit block reasons and readback, not note-only holds.

At the existing authoritative claim/lease boundary, atomically reserve/consume the one-shot authorization and bind it to the exact runtime attempt/run/lease under the existing lock order and recovery/outbox machinery. Two concurrent processes cannot both spend it. A pre-launch crash, replayed queue event, direct worker_runner invocation, task reopen, assignment change, supervisor restart or recovery must not mint/reuse a grant for a different attempt. Revalidate at actual worker entry before privileged execution, not only when a queue row was originally planned.

Keep revocation and active-run safety explicit: prevent new unauthorized effects, stop at a safe boundary, and preserve only already-authorized bounded compensation under the existing hosted lease. Do not silently call a requested or unconfirmed rollback successful. Read-only review/status/finalization must not acquire a mutation permit or clear a pending gate; a worker purpose cannot be caller-spoofed.

The task's pantheon-dev scheduler resource is serialization only, not MFA or the external exact-pair environment lease. The existing deployment/hosted protocols remain the execution owners.

### 5. Local verification required

Use isolated TaskStore/runtime roots, synthetic principals/issuer keys and local no-side-effect worker stubs/processes. Required positives: signed privileged intake with NO MFA produces durable pending record; dependency completion alone still cannot launch it; a genuine synthetic trusted/MFA/scoped/fresh test grant authorizes exactly one bound attempt; normal functional source tasks still dispatch while privileged tasks wait. Status and reviewer read-only behavior must be honest.

Required negatives: unsigned/untrusted/malformed source; claimed MFA without independently trusted proof; packet-source-only signing key; missing/wrong issuer/purpose/audience; expired/not-yet-valid/no-MFA assertion; task/generation/spec/resource/environment/action mismatch; replay/changed payload; revoked grant; two-process spend race; process loss before/after consume; restart/recovery/reassignment/reopen; delayed queue event; direct runner without canonical binding; corrupted/missing auth state; old runtime or stale command context. Assert actual zero unauthorized worker launch/side-effect, not only reason strings.

Run focused bridge/model/materialization/inbox reliability, CLI TaskStore transitions, shared admission/planner/delivery, worker-entry/lease/recovery, runtime promotion and ordinary functional dispatch regressions in bounded foreground commands. Record exact terminal commands/exits/executed counts and baseline identity. No collection-only, skipped/xfail required cases, self-asserted success, orphaned test process, weakened assertion or hidden pre-existing failure counted as green.

### 6. Exact source scope and delivery

The signed task artifact list defines source scope; it covers existing bridge/dispatch/status/runtime/worker/promotion boundaries and focused tests plus one small execution_authorization module if required to avoid copied checks. No product services, FE source, hosted deployment controller, real credentials, global model/account/quota/worker-permission changes or generic TaskStore rewrite. If an additional concrete file is necessary, checkpoint and request formal exact artifact amendment, not a parallel implementation.

Update the existing local tooling runbook and dispatch contract; preserve old signed snapshots as history and document this newer operator-authorized rule in the current first-release entrypoint. Do not modify the already accepted DOC task's frozen evidence. Include a repository-readable copy of this approved SA/SD and a precise operational command/receipt contract.

Deliver clean current-dev task worktree, scoped staging, genuine authorship/task/reviewer trailers, commit/push/PR, fresh independent exact-head review, required checks and existing integrator merge/archive. This is supervisor-dispatched tooling work, not an operator request for chatbox direct implementation.

After source acceptance, use the EXISTING qualified runtime discover-only preflight/promotion with explicit current roots and preserved active workers/leases/public verification binding; no manual runtime patch or broad restart. Prove the live runtime actually has BOTH deferred-intake and late execution barriers, including safe refusal of older unsafe runtime rollback. No live privileged grant or hosted mutation is authorized by this source task. Handoff source merge and runtime acceptance separately; source merge alone is not live readiness.

### 7. Completion of remaining dispatch, without hosted execution

Only after accepted source/runtime and no-MFA no-launch evidence: root or the existing qualified handoff workflow submits the three ORIGINAL missing task IDs DEV-RELEASE-HOSTED-001, L12-HOSTED-001, MGMT-AGORA-E2E-001 through the revised signed intake as pending authorization. Recheck canonical/pending/processing duplicates before submission; preserve original full acceptance, exact task IDs and dependency/source join.

Input: /tmp/pantheon-dispatch-dedup-20260906.0T4kBq/hosted-packet.pending-genuine-authorization.json (UNSIGNED and UNQUEUED). Its old text requiring MFA before admission must be updated ONLY in the new, not-yet-signed packet to the operator-approved execution-time rule; preserve all other genuine acceptance and source obligations. No change to historical signed packet bytes. Include this prerequisite as a dependency if the accepted contract requires it; STRUCT remains the sole product source join.

They must retain hosted work class, existing pantheon-dev resource, real implementation/reviewer identities and explicit pending-authorization state after materialization. Record processed/admitted receipt, canonical readback and the zero-launch reason; do not invent mfaVerified or authorize execution to make the dashboard green. Authenticated Management/Agora/OpenClaw, all12 loops, exact FE/BFF artifact release and rollback remain entirely unaccepted until their later genuine execution and evidence.

This planning/implementation task is not complete merely when queued or when source tests pass. No source/runtime/gate defect is hidden behind a perpetual manual owner fence. If live MFA issuer setup remains unavailable, label that later execution prerequisite truthfully; it must not prevent storing pending tasks.
