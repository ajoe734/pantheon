# Distillation SQLite lifetime repair — S4 / F08

## Defect and source repair

`DistillationJobQueue` inspected the database header with Python `open()` and
closed that descriptor. On POSIX, closing any descriptor for an inode releases
that process's POSIX record locks on the inode, including SQLite's locks. SQLite
cannot account for this external close. This can invalidate its assumptions
about other processes using the database/WAL family.

The queue also used `with sqlite3.Connection`, which commits/rolls back but does
**not** close the connection. GC-delayed connections survived across controller
ticks; each tick constructed two queue objects. Source ingestion also writes the
same queue, introduced by `53b74db51b2d7f3c65fe89ba9173b9c36d1ae53a`.

The repair stays in the existing owner:

- Remove raw header inspection and implicit non-SQLite-to-sidecar redirection.
- Close every operation's connection with `contextlib.closing`, including PRAGMA
  setup errors; retain transaction commit/rollback semantics.
- Reuse one queue object in controller `main`, its worker, and actual readback.
  The object does not retain an open connection between operations.
- Check `PRAGMA quick_check` at bootstrap before schema writes. Corruption fails
  closed. This is a startup integrity check, not a per-tick full database scan.
- Remove online quarantine/recreate. A thread lock cannot authorize renaming a
  shared database family. Source replayability and Registry idempotency do not
  imply that inbox receipts, leases or DLQ history can safely be discarded.

**Compatibility:** non-SQLite legacy JSONL now fails startup instead of silently
opening an empty sidecar. If an installation previously used `path.sqlite3`
behind a JSONL path, first verify that existing database offline and explicitly
configure its path for **all** users. This change does not migrate or delete it.
An already-corrupt queue remains unavailable; deploying this repair alone is not
recovery. Do not weaken these checks to make a deployment gate green.

## Source-owner desired reads — L2-DESIRED-OWNER-READS-20261006

The distillation controller now calls `read_source_records_for_tenant` with
its checkpoint's explicit `tenant_id`. In the configured Postgres mode, that
reader queries only `source_ingest.source_evidence` source-record payloads whose
persisted `metadata.tenant_id` exactly matches the requested tenant, under a
read-only transaction. It does not bootstrap the source table, use the JSONL
fallback, materialize foreign rows, or adopt records without tenant metadata.
Missing/invalid tenant scope and owner-store errors fail the desired read; they
must not be reported as an empty healthy tick. JSONL remains supported for
local/test use, but its repository lookup is tenant-indexed by the same
explicit scope.

`strategy-distillation-worker` now receives the same configurable evidence
backend, DSN and table as source-ingest. Its existing queue and controller
checkpoint are unchanged. Regression tests persist a tenant-scoped normalized
record, exercise distillation admission/claim and Registry terminal readback,
and assert the Postgres read path is read-only and tenant-filtered. These are
local synthetic proofs only; they do not establish hosted availability or
perform deployment, restart, replay, or recovery.

## Evidence and limits

On immutable base `b06a22610f41934c801cc7c7da7b373bc33aa8db`, a live SQLite read
transaction blocks an independent process from exclusively locking SQLite's
main-file shared-byte range. Constructing another queue lets the probe acquire
that lock. The repaired class keeps it blocked. The probe retains a connection
and disables GC deliberately; it does not rely on a same-process lock test.

The eight new regression cases produced **6 failures / 2 passes** on that base.
One of three two-process trials raised SQLite `disk I/O error`; the other two
passed. This is not a claimed independent reproduction of the operator's separate
12/12 corruption experiment, nor the exact hosted error. The repaired eight
cases passed; repeated two-process testing completed **12/12** trials without
loss, quarantine, failed integrity checks or missing synthetic inbox receipts.

Metadata-only VM observation at `2026-10-06T02:53:28Z` found controller PID
2492083 retaining 17 main-DB and 13 WAL descriptors. Five samples showed no lock
on DB inode 4205335, while SHM inode 4205287 retained a READ lock. Existing DB
birth time was `2026-10-05T07:22:58Z`, matching a retained quarantine family and
receipt. The current containers started on October 6 at 02:19; absence of
October 5 log messages from these new containers cannot exclude older events.
No DB/WAL/SHM contents were opened by this observation.

These observations support the mechanism; they do not establish every historical
corruption event, exact lost-data count, or recoverability. Single FD/lock
snapshots without transaction and inode context are not sufficient by themselves.

## Coordinated recovery prerequisites — not performed by this change

1. Hold the official dev deployment/maintenance lane so deployments and restart
   automation cannot recreate users during recovery. Record exact participating
   images, configuration and host identity from the current environment plan.
2. Quiesce `source-ingest`, `source-ingest-scheduler`, and
   `strategy-distillation-worker`. Enumerate **all** FD/inode users of the shared
   volume, including manual tools and other containers; names alone are not a
   proof of isolation. Confirm connections have closed. Service shutdown itself
   can checkpoint WAL; capture non-invasive metadata before stopping.
3. Preserve the stopped DB/WAL/SHM family, prior quarantines, receipts and
   timeline consistently, with hashes and original paths/inodes. Never inspect a
   live SQLite file with ordinary Python file handles inside a SQLite-using
   process. Never rename just the main DB while another process retains it.
4. Diagnose only isolated copies. Compare recoverable source snapshots/versions,
   outbox/inbox/DLQ, upstream evidence and actual Registry terminal receipts.
   `quick_check` passing proves neither logical completeness nor replayability;
   use full integrity/relational and business-identity checks as appropriate.
5. Establish the exact recovery set and explicit unknown/loss bounds before any
   replacement or replay. Publish a consistent family only while all users are
   fenced; preserve the original. Do not silently reset a mismatched controller
   checkpoint or bypass tenant/environment and idempotency fences.
6. Restart accepted repaired images under the coordinated lane, then verify
   natural ingestion, claims, Registry readback, durable receipts and a stable
   restart across multiple ticks. Only that fresh evidence can qualify L2.

No shared service stop, restart, file replacement, replay, deployment or hosted
business write is part of this source change. Local synthetic acknowledgements
exercise SQLite ledger integrity, not real Registry delivery or L1–L12 closure.
