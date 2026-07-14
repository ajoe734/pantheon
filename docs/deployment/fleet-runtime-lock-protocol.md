# Fleet runtime, task-state, and activity-audit lock protocol

Status: protocol version 1 implementation and bootstrap runbook  
Protocol ID: `pantheon-runtime-task-audit-lock-v1`  
Bootstrap task: `LOOP-PROD-RUNTIME-BOOT-001`

## Purpose and authority

This protocol serializes Pantheon's file-backed runtime admission, canonical
task state, and activity audit planes. It is a prerequisite to the
`loop-product-level-remediation-2026-07-13` dispatcher. It does not, by
itself, authorize materializing the 48 primary tasks.

The desired-state authority is the bootstrap task contract. The actual-state
authorities are the three runtime sources, `ai-status.json`, the active and
rotated activity logs, the protected capability-verifier decision, and the
post-merge zero-write dry-run evidence. Missing or contradictory authority
fails closed.

The protocol is ready for authoritative use only when all of these are true:

1. the implementation, exact writer registry, and reviewer-signed bootstrap
   completion record are merged into `dev`;
2. the exact merged writer blobs are still present in the executing checkout;
3. a root-controlled verifier policy and accepted ledger entry are installed
   outside the repository;
4. the live capability manifest binds that merged commit and passes the
   protected verifier;
5. the bootstrap task is exactly `done`; and
6. the planning dispatcher completes a strict live `--dry-run` without
   changing any canonical source.

Do not run `--apply` while any item remains pending.

## Stable lock planes

All three locks are sidecar files. A canonical data file may be atomically
replaced, rotated, or truncated without changing the lock inode used by
contenders.

| Rank | Plane | Stable path | Shared use | Exclusive use |
| ---: | --- | --- | --- | --- |
| 1 | `runtime_admission` | `.orchestrator/runtime-admission.lock` | consistent runtime/event/approval inspection and dry-run admission | queue/event, worker, approval, watchdog, inbox, and supervisor RMW |
| 2 | `task_state` | `.orchestrator/task-state.lock` | canonical task-state inspection | complete `ai-status.json` transaction and archive/status transition |
| 3 | `activity_audit` | `.orchestrator/activity-audit.lock` | active and rotated audit scan | append, rotate, recovery, replay, prune, and readback |

The only legal nested order is:

```text
runtime_admission -> task_state -> activity_audit
```

A caller may stop at any rank. A caller holding a later rank must never acquire
an earlier or peer rank. The common lock helper rejects reverse or peer-rank
nesting before entering the kernel. Re-entry of the same path in the same
calling thread is permitted; a shared hold cannot be upgraded to exclusive.

Shared locks use `LOCK_SH`; exclusive locks use `LOCK_EX`. The dispatcher
always requests all locks non-blocking. A contended lock therefore produces a
fail-closed `runtime/task/audit lock set is busy` result rather than waiting on
an unbounded maintenance transaction. Normal runtime helpers may use blocking
locks when their bounded operation requires serialization.

Never unlink, replace, rename, truncate, or deploy over the sidecar lock files.
They may be created once with `open(..., "a+")` and persist across process
lifetimes. Deployment cleanup must exclude them.

## Transaction boundaries

The lock protects a transaction, not an individual `read_text`, `write_text`,
or `os.replace` call. A registered writer must hold its plane's exclusive lock
across load, strict validation, mutation, flush/fsync, replace or append,
directory fsync where applicable, and post-write readback.

### Runtime plane

`.orchestrator/runtime_state.py` owns the stable runtime primitive. The
supervisor holds the exclusive runtime lock around its whole-file state and
queue RMW. Approval creation/resolution and suspend/resume, file-inbox
delivery, watch-event enqueue/scans, and watchdog recovery use the same lock.
Runtime operations that append audit entries nest the activity lock after the
runtime lock.

An unlocked runtime projection is permitted only for a display that already
holds the later task-state lock. It must not drive an admission decision,
write, retry, or dispatch.

### Task-state and audit planes

`scripts/ai_status.py` holds the task-state lock for the full command
transaction. Read-only status commands use a shared task lock. Mutations use
an exclusive task lock, write a pending activity outbox into the durable task
state, then acquire the activity lock to append/recover the exact audit events.

Audit readers scan the active file and both supported rotated-archive layouts
under a shared activity lock. Audit writers rotate and append under an
exclusive activity lock. Rotation archives the prior bytes and truncates the
active audit file in place, preserving its inode for any open append handle;
the sidecar, not the audit file inode, is the serialization authority.

### Planning dispatcher

`scripts/dispatch_loop_product_level_remediation_2026-07-13.py` is the only
registered planning dispatcher in protocol version 1. It uses shared locks for
`--dry-run` and exclusive locks for `--apply`. The runtime guard remains held
while the dispatcher enters task state and activity audit, validates the
proposed status/outbox transaction, and either reports zero writes or commits
the transaction.

`--validate-only` validates repository contracts and the DAG; it is not live
runtime proof and does not satisfy the bootstrap dry-run requirement.

## Strict runtime admission decision

`tasks_runtime_admission_guard` parses these sources in this exact order while
holding the runtime lock:

1. `.orchestrator/state.json` (`version: 2`, object workers, object
   `queue.events`, and non-empty worker task IDs);
2. `.orchestrator/event-queue.jsonl` (non-empty JSONL, object rows, unique
   non-empty `event_id`, and non-empty `task_id`); and
3. `.orchestrator/approval-queue.json` (`version: 2`, list `pending`, list
   `history`, and non-empty pending task IDs).

UTF-8 decoding and JSON duplicate-key checks are strict. Missing, empty,
unreadable, malformed, wrong-version, or default-substituted sources return
`runtime_source_invalid`. They are never normalized to an empty structure for
admission.

The decision has exactly these ten fields:

```json
{
  "schema_version": 1,
  "protocol_id": "pantheon-runtime-task-audit-lock-v1",
  "strict": true,
  "lock_mode": "shared",
  "task_ids": [],
  "source_sha256": {
    "runtime_state": "...",
    "event_queue": "...",
    "approval_queue": "..."
  },
  "conflicts": [],
  "allowed": false,
  "reason_id": "...",
  "snapshot_sha256": "..."
}
```

`source_sha256` preserves source order. `snapshot_sha256` is SHA-256 of the
canonical JSON encoding of that ordered digest object. Conflict records are
deduplicated and sorted, and contain exactly the source, task, status, and
record identity observed in the runtime snapshot.

| Condition | Stable `reason_id` |
| --- | --- |
| no task IDs | `task_ids_empty` |
| blank or non-string task ID | `task_ids_invalid` |
| duplicate task ID | `task_ids_duplicate` |
| any invalid runtime source | `runtime_source_invalid` |
| caller did not request strict mode | `strict_required` |
| target is queued, started, running, approval-suspended/pending, in retry/fallback/stall, or otherwise admitted | `target_has_runtime_admission` |
| exact source schemas and no target conflict | `clear` |

The dispatcher accepts only `allowed: true`, `reason_id: "clear"`, and an
empty conflict list. It then strictly parses canonical task state and the
present audit sources under the next two locks. A genuinely empty
pre-dispatch audit is valid only when outbox preflight finds no conflicting
event. Byte-substring scans are not an admission mechanism.

## Durability and recovery

### Atomic replacement

Whole-file JSON writers flush and fsync the temporary file, atomically replace
the canonical file, fsync the parent directory, and read back when the
transaction contract requires it. Queue replacement follows the same pattern.
The sidecar remains open across the complete sequence, so a contender cannot
cross the old data-file inode after `os.replace`.

Append-only writers flush and fsync before releasing the activity or runtime
lock. A process exit or `SIGKILL` causes the kernel to release its `flock`; it
does not remove the stable sidecar. The next process must inspect durable
outbox/state before accepting new work.

### Status activity outbox

Each `ai_status` event has a deterministic content-derived `event_id`. A task
mutation commits this exact outbox into `ai-status.json` before audit append:

```json
{
  "schema_version": 1,
  "transaction_id": "ai-status-tx-<sha256>",
  "events": []
}
```

Recovery scans the active and rotated audit sources, rejects duplicate IDs in
one source and conflicting payloads across sources, appends only missing exact
events, reads them back, and only then clears the outbox through another
durable task-state write. A crash before status commit has no admitted audit
work; a crash after status commit leaves the outbox; a crash during append is
idempotently repaired; and a crash after append but before clear observes the
existing payload and does not duplicate it.

The planning dispatcher uses its own content-addressed
`program_activity_outbox` with the same status-before-audit and exact-replay
principle. Pending outbox recovery must finish before a new program
transaction is created.

## Exact writer registry and historical boundary

Protocol version 1 requires an exact nine-path registry:

1. `.orchestrator/runtime_state.py`
2. `.orchestrator/supervisor.py`
3. `.orchestrator/common.py`
4. `.orchestrator/approval_queue.py`
5. `.orchestrator/adapters/file_inbox.py`
6. `.orchestrator/watch_events.py`
7. `.orchestrator/supervisor_watchdog.py`
8. `scripts/ai_status.py`
9. `scripts/dispatch_loop_product_level_remediation_2026-07-13.py`

`.orchestrator/runtime-task-audit-writer-registry.json` must contain exactly:

```json
{
  "schema_version": 1,
  "protocol_id": "pantheon-runtime-task-audit-lock-v1",
  "transaction_scope": "complete_read_validate_mutate_replace",
  "direct_canonical_writes_forbidden": true,
  "writers": {}
}
```

Every writer digest is SHA-256 over the exact committed bytes. The registry is
generated only after all nine blobs are frozen for review.

The repository also contains historical `scripts/dispatch_*.py` programs and
other maintenance utilities that predate this protocol. They are not silently
covered by the nine-path registry. Until each direct canonical writer is
migrated to the shared helpers or made non-authoritative and technically
unable to target the canonical status root, it must not be executed against a
live Pantheon status root. A naming convention or operator promise is not
proof of exhaustive serialization.

Final bootstrap evidence must include a tracked static inventory result that
rejects unregistered direct writes. If a historical dispatcher remains
runnable against `ai-status.json`, `ai-activity-log.jsonl`, runtime state,
event queue, or approval queue, the writer-registry acceptance item remains
blocked and no capability manifest may be installed.

## Protected capability ceremony

Three different artifacts serve different purposes:

- the committed writer registry binds the implementation blobs;
- committed `completion.json` carries the distinct reviewer verdict and
  Ed25519 signature; and
- the uncommitted live capability manifest binds the exact merged commit and
  is accepted only through a protected external policy and ledger.

### 1. Freeze and review the candidate

Run the full exact-head checks, save a redacted machine-readable checks report,
and calculate its SHA-256. Generate the writer registry from the frozen nine
blobs. The owner identity is `Codex2`; the reviewer identity is `Codex` and
must independently inspect and run the exact candidate.

The reviewer, not the owner, creates the final signature for `completion.json`.
Its schema is exact and contains:

```text
schema_version, task_id, task_contract_sha256, conclusion,
worker_runtime_identity, reviewer_runtime_identity, checks_sha256, verdict_id,
verifier_capability_sha256, signature_algorithm, key_id, policy_version,
signature, revocation_checked_at, ledger_entry_id
```

`conclusion` must be `passed`; identities must be allowed and distinct;
`signature_algorithm` must be `ed25519`; and
`verifier_capability_sha256` must equal the registered
`.orchestrator/runtime_state.py` digest. The signature covers the canonical
payload produced by `runtime_capability_signature_payload`, excluding only the
`signature` value itself. Private key material never enters the repository,
logs, evidence bundle, or worker environment.

Commit the signed completion record, registry, implementation, tests, and
runbook in the primary bootstrap PR. Do not claim a merge SHA before GitHub
actually merges it.

### 2. Install the post-merge manifest and protected policy

After merge, call the exact merge SHA `M`. Confirm that `M` exists and is an
ancestor of `refs/remotes/origin/dev`. Read every registered blob, the
registry, and `completion.json` from `M`, and reject any working-tree digest
that differs.

Create `.orchestrator/runtime-task-audit-lock-capability.json` in the live
checkout. It is runtime state, is git-ignored, and must contain exactly the
fields required by the dispatcher: protocol identity, module/API/lock
contract, all nine writer digests, registry path/digest, executing dispatcher
digest, bootstrap task/contract binding, completion path/digest, and `M`.

The verifier policy is not a repository artifact. The supervisor/dispatcher
environment must set `PANTHEON_RUNTIME_LOCK_VERIFIER_POLICY` to a canonical
absolute path outside the repository. The target and every parent directory
must be real paths, never symlinks, owned by root, and not group- or
world-writable. Prefer mode `0600` for the policy file. An ignored or default
path inside the checkout is not an accepted production policy.

The exact policy contains protocol/policy/key identity, the Ed25519 public
key, the revoked-key list, and protected ledger entries. An accepted ledger
entry binds the completion verdict and reviewer to `M`, the capability
manifest digest, registry digest, completion digest, revocation-check time,
and `status: "accepted"`. A root-controlled operator installs the entry only
after the distinct reviewer verdict and merge readback are complete.

Restart the supervisor with the exact `M` runtime identity and the external
policy path. `verify_runtime_lock_capability` must return exactly:

```json
{
  "schema_version": 1,
  "protocol_id": "pantheon-runtime-task-audit-lock-v1",
  "allowed": true,
  "reason_id": "verified",
  "manifest_sha256": "...",
  "writer_registry_sha256": "...",
  "completion_evidence_sha256": "...",
  "merged_commit_sha": "..."
}
```

Format-valid, locally self-authored, revoked-key, wrong-policy, writable-path,
unledgered, wrong-blob, or non-`dev`-ancestor manifests fail closed.

## Post-merge strict dry-run

The dispatcher requires the bootstrap task to be exactly `done`, while task
closeout requires the implementation PR to be merged first. The evidence flow
is therefore deliberately two-phase:

1. primary PR: implementation, frozen registry, reviewer-signed
   `completion.json`, tests, and premerge evidence plan;
2. independent review, primary merge, and owner closeout to `done`;
3. protected manifest/policy/ledger installation and exact-merge supervisor
   restart;
4. live zero-write `--dry-run`; and
5. evidence-only follow-up PR containing immutable redacted run artifacts,
   final `evidence.json`, and `evidence.sha256`.

The follow-up must not change a registered writer, the registry, or
`completion.json`. Primary catalog materialization remains unauthorized until
the dry-run evidence is accepted and merged.

During the documented maintenance window, prove no unregistered writer or
runtime producer is active. Capture SHA-256 and file identity for, at minimum:

- `.orchestrator/state.json`
- `.orchestrator/event-queue.jsonl`
- `.orchestrator/approval-queue.json`
- `ai-status.json`
- `ai-activity-log.jsonl`
- every audit archive considered by outbox preflight
- the capability manifest, registry, completion record, executing dispatcher,
  and all registered writer blobs

Then run from the exact merged checkout:

```bash
AI_NAME=Codex2 \
PANTHEON_STATUS_ROOT=/absolute/canonical/pantheon-root \
PANTHEON_RUNTIME_LOCK_VERIFIER_POLICY=/absolute/protected/runtime-lock-policy.json \
python3 scripts/dispatch_loop_product_level_remediation_2026-07-13.py --dry-run
```

Capture stdout, stderr, exit code, admission decision/lock trace, process
identity, checkout HEAD, `origin/dev` ancestry, and before/after source hashes.
Success requires exit code zero, the message `zero writes performed`, and
identical before/after hashes. A busy lock, pending/conflicting outbox,
malformed source, target runtime conflict, verifier rejection, changed file
identity, or any canonical write is a failed dry-run.

## Required validation matrix

The final evidence bundle must bind exact commands and outputs for:

- global order traces and shared/exclusive/non-blocking behavior for all three
  locks;
- post-`os.replace` stable-sidecar contention;
- holder exit and `SIGKILL` release;
- malformed, missing, empty, unreadable, wrong-version, duplicate-key, busy,
  duplicate-ID, foreign-ID, and active-admission rejection;
- exact decision shape, source ordering/digests, aggregate digest, conflicts,
  and reason IDs;
- concurrent runtime enqueue and status writes without lost newest truth;
- audit rotation during append/scan;
- crash before/after status commit, during audit append, and before outbox
  clear, with exactly-once replay;
- static rejection of every unregistered direct canonical writer;
- exact writer/dispatcher/registry/completion/manifest blob bindings;
- Ed25519 signature, active key, external protected policy, revocation result,
  accepted ledger, exact protected-verifier decision, and `dev` ancestry; and
- canonical post-merge dry-run with zero-write before/after hashes.

Test-only protocol shims, a local branch, an open PR, a process scan without
the locks, or a restarted process without merged and protected evidence do not
satisfy this protocol.

## Failure response and rollback

On any validation or runtime failure:

1. do not run dispatcher `--apply`;
2. preserve the sidecars and pending outboxes;
3. stop unregistered writers and record the observed process identities;
4. capture redacted source hashes and the exact fail-closed reason;
5. revoke or remove the live capability/ledger acceptance if a bound blob,
   key, policy, or merged identity is no longer trusted; and
6. repair through a new reviewed task PR, then repeat the full post-merge
   ceremony.

Deleting a lock or outbox is not recovery. Restoring an older whole-file
snapshot without first reconciling newer runtime/task/audit truth is forbidden.
