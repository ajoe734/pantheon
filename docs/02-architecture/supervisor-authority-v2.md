# Supervisor Authority V2: Task-State Store

Status: authoritative for Supervisor V2 task-state persistence  
Effective: 2026-08-11  
Owner: Supervisor control plane

This specification replaces the task-state persistence proposal in
`SUPERVISOR_REWRITE_PLAN.md`. That earlier plan remains historical design and
incident context; it does not authorize a V1 compatibility path.

## Authority and responsibilities

There is one authoritative task-state writer: the governed supervisor/status
command path. It enters one V2 store transaction and commits a task-board
transition. The derived `ai-status.json` projection is never a competing source
of truth: it is updated only after a durable V2 transition and may be repaired
from the compact V2 head.

| Component | Owns | Must not do |
| --- | --- | --- |
| V2 task-state store | transition validation, one store lock, durable delta-before-head commit, head CAS, crash-tail projection | schedule workers, change lifecycle policy, scan the V1 archive on a hot read |
| Governed status command / supervisor | construct a candidate board, call the one transaction API, update the derived projection | append directly to a journal or select an alternate state authority |
| Offline verifier | full V2 transition-chain and frozen-archive verification | run in a scheduler tick or canonical mutation |
| Migration tool | freeze and identify a V1 journal once, write V2 genesis | delete, truncate, or silently fall back to V1 |

The V2 store lock is `<event-log>.lock`. All V2 reads and mutations use it;
`snapshot_transaction()` is the one mutation API for multi-save governed
commands. A projection-level lock may serialize its file replacement, but it is
not a second state writer and cannot supersede the V2 head.

## Persistent layout and lifecycle

For configured runtime event-log path `task-state-events.jsonl`, V2 keeps these
siblings in the git-external runtime directory:

| Artifact | Mutability | Purpose |
| --- | --- | --- |
| `task-state-events.jsonl` | append-only | V2 compact transition deltas |
| `task-state-events.jsonl.head.json` | atomically replaced | current full board, sequence, delta offset, state/head digests, archive identity |
| `task-state-events.jsonl.v1.archive.jsonl` | read-only | exact frozen legacy bytes |
| `task-state-events.jsonl.archive.json` | immutable after migration | archive length, final V1 sequence, journal SHA-256, projected state SHA-256 |
| `task-state-events.jsonl.lock` | lock file | the V2 store's sole serialization point |

A transition records its sequence, predecessor event digest, base-state digest,
resulting-state digest, provenance, archive identity, and a deterministic
JSON-like delta. It does not persist a full board. A genesis transition may be
large because it establishes the initial board; subsequent ordinary field
changes are only their changed paths.

The mutation lifecycle is fixed:

1. Hold the exclusive V2 lock and load the current head plus any tail after its
   recorded byte offset.
2. Reject a stale expected sequence, invalid delta, invalid transition, or an
   unaudited disappearance of nonterminal task rows.
3. Append the compact transition, `fsync` its file, then `fsync` its directory.
4. Atomically replace the head only when its recorded head digest still matches
   the generation used by the writer (head CAS), then `fsync` the directory.
5. Release the lock; only then does the caller refresh `ai-status.json`.

A crash before step 3 changes nothing. A crash between steps 3 and 4 leaves one
durable, valid tail transition. The next read replays only bytes after the
head's offset; the next successful writer incorporates that tail into its new
head. An incomplete, malformed, conflicting, or corrupted tail is rejected,
not truncated or guessed.

## Invariants and failure semantics

| Invariant | Enforcement | Failure result |
| --- | --- | --- |
| The head has a self-digest, exact sequence, state digest, and transition offset. | Strict schema/digest validation and CAS. | Fail closed: stale head CAS or integrity error. |
| Every transition applies to the exact predecessor. | Base-state, predecessor hash, sequence, delta/result digests. | Fail closed; no projection write. |
| Live tasks cannot silently disappear. | Identity-aware nonterminal census and audited drain marker. | Reject before append; existing bytes remain unchanged. |
| The V1 archive remains attributable. | Genesis/head bind archive identity; offline verifier checks exact bytes and V1 final state. | Offline integrity failure; never scheduler hot-read failure. |
| A normal read has no V1 dependency. | Reads head plus only post-head delta bytes. | Missing V2 head is migration-required, never V1 fallback. |
| A tail cannot conceal a torn write. | JSONL newline boundary and strict transition replay. | Fail closed as corrupted tail. |

The 2026 sequence-1592 empty-board incident is a validation case: a candidate
that removes still-live identities must fail before a new transition is written.
It is not a reason to preserve the old full-state journal/checkpoint mechanism.

## Hot-path and verification budgets

Normal `load_snapshot()` and `append_state_commit()` have these hard limits:

- Do not `mmap`, hash, parse, or open the frozen V1 archive.
- Do not replay a V1 full-state journal, scan a V2 prefix, or maintain a
  checkpoint cache.
- Read one compact head and, only after an interrupted delta-before-head commit,
  replay the tail after `head.delta_offset`.
- A canonical mutation writes one delta and one atomic head; it does not call
  the offline chain verifier.

`verify_full_chain()` and `scripts/verify_task_state_store.py` are offline
operations. They may parse every V2 delta and validate the frozen V1 bytes,
their hash, final sequence, and projected-state digest. They must not be called
from scheduling, dispatch admission, a canonical state mutation, or a status
command's normal read path.

The focused test suite builds a synthetic multi-megabyte legacy journal,
migrates it, then proves repeated hot reads succeed while archive parsing is
forbidden. It also covers head-CAS conflict, V2 delta corruption, torn tail,
delta-before-head crash recovery, archive tamper detection, and nonterminal
task-loss rejection.

## Explicit deletion map and remaining callers

| Deleted V1 production mechanism | V2 replacement |
| --- | --- |
| full-board `state` in every append event | one current head plus compact path deltas |
| `mmap`/whole-journal prefix hash on each snapshot | one head file and a bounded post-head tail |
| checkpoint sidecar, cache, forced full-replay environment switch | no runtime checkpoint; offline `verify_full_chain()` |
| full-journal append readback/replay | fsynced delta write followed by head CAS |
| V1 automatic read fallback | explicit one-time migration, otherwise fail closed |

The remaining production callers are `common.load_status` /
`common.write_status`, `scripts/ai_status.py`, supervisor reconciliation, and
dispatcher snapshot admission. They use `load_snapshot()`,
`snapshot_transaction()`, or `append_state_commit()`; none may call
`load_events()` or `project_latest_state()` in a hot path. The latter two are
offline audit compatibility readers that reconstruct state from V2 deltas only.

Gross source accounting for this delivery is recorded from
`git diff --numstat origin/dev...HEAD` before review. The expected shape is a
net deletion of the V1 replay/checkpoint implementation and obsolete tests,
with additions limited to the V2 store, migration/verifier tests, and this
specification. Review must reject any remaining production caller of a V1
full-state replay/checkpoint symbol.

## Rollout, migration, and rollback

Preconditions: stop concurrent mutation through the existing canonical lock,
take a normal runtime backup, and run the migration tool with `--dry-run`.

1. Run `scripts/migrate_task_state_store_v2.py --event-log <absolute-path>`.
2. Confirm `migrated`, archive identity, and V2 sequence 1.
3. Run `scripts/verify_task_state_store.py --event-log <absolute-path>` and
   require projection parity plus full-chain/archive verification.
4. Start the V2 runtime and verify a normal status read/mutation uses only the
   head and delta tail. Keep the V1 archive read-only indefinitely.

Migration is resumable only from the preserved archive. If it stops after the
archive rename or after durable genesis delta but before head replacement, the
next migration invocation rebuilds the V2 head from the small V2 delta log. It
never deletes the archive.

Rollback is a deliberate release operation, not an automatic runtime fallback:

1. Stop V2 writers and preserve the V2 head, delta log, manifest, and archive.
2. Independently verify the V2 chain and archive; do not rename or overwrite
   historical artifacts.
3. If an older binary must run, an authorized operator creates a separately
   named V1 recovery journal from the frozen archive plus one validated
   full-state recovery event made from the V2 head, and explicitly binds only
   that older process to the new path.
4. Re-run V2 migration against that recovery path for roll-forward. No process
   may silently select V1 because a V2 head is missing or invalid.

This preserves evidence, makes the data-loss boundary explicit, and converts a
bad V2 head into a fail-closed incident rather than a quiet compatibility mode.
