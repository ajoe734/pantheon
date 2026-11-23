# Research meeting action owner handoff

Task RESEARCH-MEETING-ACTION-TICKETS-20261123; owner Codex; reviewer Antigravity.
Source baseline `bd47d73bc4d987309bf2f60f20ce2fb7b05cfda4` (current origin/dev
at initial tracing and final fetch). Component implementation awaiting independent
review and canonical integration. No hosted deployment or acceptance is claimed.

CI repair: the first PR head was returned by the integrator for shard 3's
ordinary RW-01 store compatibility regression. See [ci-repair.md](ci-repair.md)
and [ci-repair-validation.txt](ci-repair-validation.txt) for the cause, corrected
boundary, refreshed dev base and bounded rerun results. The changed head is
submitted to Antigravity for fresh exact-head review.

## Caller and storage trace before editing

- `services/research/main.py` already exposes POST/GET/list/PATCH
  `/api/research/tickets` and POST/GET/list `/api/research/notes`.
- `ResearchWriteOwner` owns the existing `research.research_tickets`,
  `research.research_experiments` and `research.research_notes` JSONB tables.
  `PostgresJsonOwnerStore.compare_and_set`, transaction/advisory locks and
  its insert-on-conflict semantics were inspected before implementation.
- BFF `research/client.py`, `research/service.py`, `research/routes/tickets.py`,
  `ports/research_knowledge_source.py` and `ports/read_surface_ports.py` serve
  ordinary authenticated operator ticket CRUD and historical reads. Their
  meeting identity/action forwarding belongs to BFF-MEETING-ACTION-HUMAN-INBOX-20261123.
- `services/persona/pool_autonomy_cycle.py`, BFF `personas/service.py` and
  `persona_strategy_discovery.py` create ordinary discovery tickets. Existing
  research demand reconciliation measures post-demand pool admissions; it is
  untouched and cannot discharge document-delivery acceptance.
- Existing `tests/research_write_owner/test_research_write_owner.py` covers
  legacy CRUD and independent owner reads, and `test_research_single_owner_routes.py`
  exercises Research HTTP routing. Actual saved-artifact ownership is resolved
  by `_saved_artifact_tenant` from artifact/run/task records. Meeting evidence
  uses that same scoped owner and rejects unresolved tenants. Meeting-bound
  notes add an owner-stamped immutable binding in the existing note table.

## Delivered owner boundary

The exact producer contract is [contract.md](contract.md). API detail and page
responses are real owner projections. Stable source IDs, a source digest and
transaction-scoped group locking prevent duplicate creates, conflicting replays
and cap races. CAS revisions and actor/key/payload digests preserve all state
transitions and make exact retries no-ops. A submission is reviewable, with
status in_progress; only an authorized reasoned acceptance re-resolving the
submitted evidence closes the obligation. Returns preserve prior evidence and
allow a corrected submission. Human input resumes the same accountable Persona
and original ticket ID. A role label, generic ack or pool membership is not evidence.

Both direct in-process owner calls and HTTP meeting paths require an authenticated
tenant/actor. The service validates bearer claims; body actor/tenant impersonation
is rejected. Meeting reads filter tenant and authorized assignee/reviewer/required
human or the original tenant controller. Meeting queues exclude all ordinary or
missing-tenant historical records. Existing ordinary callers retain their old
list shape and normal CRUD. Meeting pages use tenant/owner/series filters and a
scoped stable ticket-ID cursor, with a SQL LIMIT before transferring wide rows.
Due/submitted/updated filters retain old obligations across weekly meeting IDs.

No obsolete endpoint or historical reader was removed: each still serves those
ordinary supported callers. The former generic experiment create/retry link
writer is explicitly bypassed for meeting-linked tickets, because its unscoped
whole-row update could erase action history. Ordinary experiment linking remains;
meeting execution evidence is linked through the authenticated submit transition.

## Validation and budgets

See [evidence.json](evidence.json), [storage.json](storage.json) and
[storage-check.json](storage-check.json) for commands, terminal results, actual
PostgreSQL JSONB byte measurements and capacity accounting. Local tests use an
isolated PostgreSQL16 container, unique per-test schemas and actual Research
HTTP create/detail/list/update endpoints, not a substitute ticket repository.
The executable [probe.py](probe.py) reads an existing hosted ticket and its full
series without writes; its real HTTP fixture execution verifies immutability.
Hosted proof must be supplied by the integrated chain on the deployed pair.

The chain manifest records original ticket/note tables, consultation lifecycle
and outbox snapshots, dispatch ledger and existing Persona OODA packets.
New source/result inserts are bounded; existing history is preserved. Whole-table
retention, MVCC/WAL and whole-ledger historical growth remain RC3 WP3.1/3.3 debt,
explicitly marked unbounded. Producer-specific prompt/cycle counts and measured
bytes belong to the meeting and Persona tasks and remain unverified until their
normal-entrypoint evidence; owner component tests do not substitute for that proof.

## Residual findings and owning disposition

1. **BFF saved-artifact surface metadata regression, RC6 existing research/read-source
   owner.** `tests/research_write_owner/test_artifact_read_owner.py::test_bff_routes_distinguish_data_empty_unavailable_and_missing`
   expects source=service_client but receives typed_store. It fails identically
   on this branch and an archived, unchanged baseline bd47d73bc. The exact test
   and both terminal outputs are retained in evidence. No BFF source is changed
   here. Coordinator must attach this instance to RC6's existing research page /
   source-metadata work package; this task does not open a symptom-specific fix.
2. **Whole-writer historical growth, RC3 WP3.1/3.3 owner.** Original ticket/note
   tables and consultation dispatch ledger have no verified global TTL/cap.
   Per-origin/per-ticket caps are enforced here; automatically deleting unresolved
   obligations or referenced evidence would violate the original work contract.
   Keep the existing RC3 class obligation open and measure hosted physical growth.
3. **Producer and hosted integration obligations, original M/P/acceptance owners.**
   The new contracts require consultation-svc to authenticate as service, the
   existing Persona controller to forward its tenant and verified identity, and
   BFF to forward the actual human principal. Effective capability checks,
   frozen chair/cycle envelopes and cross-meeting real owner execution are owned
   by their existing parallel tasks. These are pending integration proof, not
   successful executions inferred from this component fixture.
