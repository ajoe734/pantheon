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

## Human inbox scope correction (2026-11-23)

The reopened owner scope adds `human_actionable=true` to the existing
`meeting_linked=true` GET and RW-01 SQL query. See [contract.md](contract.md)
for its exact predicate and authenticated visibility. BFF-MEETING-ACTION-HUMAN-INBOX-20261123
should use this one bounded page instead of selecting only waiting_for_input or
scanning ordinary Persona work. FE-MEETING-ACTION-FOLLOWTHROUGH-20261123 owns
displaying `submit` for direct human assignments and `provide_input` for the
Persona input path. Each PATCH still targets the original owner endpoint with
the original verified principal, expected_revision and idempotency_key.

The local real-PostgreSQL/Research-HTTP regression puts 24 visible but unrelated
Persona records before every actionable row. Two-row pages still cover all four
human obligations, including open, in-progress, returned and requested input,
without exposing other humans' or tenants' rows to an unrelated viewer. Tenant
operator/admin and existing controller reads retain their original visibility.
The direct human path is exercised through saved note, submit, review return,
corrected submission and acceptance; the separate Persona-input path still
resumes the same Persona and ticket ID. Responses retain the current revision.

No caller, table, index or writer is replaced by this optional read filter. The
old owner/series/time/state queries and ordinary list shape serve their existing
distinct callers. Cursor scope gains the true-filter flag; false/default cursors
retain their old fingerprint. Storage cadence, count/size, growth/day, per-record
caps and cleanup remain as recorded in storage.json and the chain manifest:
this change adds zero persisted bytes, writes, recurring jobs or daily growth.
Reads are on demand, one SQL page of at most page_size+1 rows (maximum 101),
returning at most 100 summaries. Existing 256 KiB encoded ticket caps apply.

Source validation and exact commands are recorded in human-actionable-validation.txt
and evidence.json. Existing RC2 integration/hosted, RC3 retention and RC6 metadata
residuals below retain their owners; this correction adds no new residual class.

## Inline human deliverable correction (2026-11-23)

The next reopened scope starts from `ad5dec939`, with `origin/dev` at
`0c806ffed` already contained. Before editing, traced the existing ticket PATCH
allowlist, KW-02 `_create_meeting_note`, scoped saved artifact/note reader in
`main.py`, transaction/CAS implementation and actual HTTP/PostgreSQL tests.
`human_actionable` is preserved. Code anchor: `7d7068861`.

PATCH submit/provide_input now accepts bounded `evidence_note={title,body,
source_refs?}` alongside optional reference-only evidence. It calls the original
KW-02 writer with the authenticated actor and original ticket, inside the same
transaction as the original ticket CAS. Row locking serializes concurrent commands;
revision/permission checks run before note creation. A failed insert, capacity
check or ticket write rolls back both. Exact retries write zero rows/events;
conflicting content cannot replace the report. The corrected-report path uses a
new command key and preserves prior bodies/history. No second ticket is created.

Original-ticket authorized detail and PATCH responses resolve `evidence_notes`
with the real saved body, sources, checksum and stamped human identity. Submission
and supplied human input stay distinct reference envelopes. The original Persona
and reviewer read the report on that same ticket. Human input returns the same
owner/id to in_progress. Exact response recovery is allowed for the original input provider even after
further owner transitions: existing history reconstructs the input revision only,
so retry cannot reveal new private results without normal ticket visibility.
No additional general read/list permissions are introduced.

The canonical next instruction also requested the existing direct human ticket
description on `human_actionable=true` pages. It is projected only for human
assignments, from the same bounded SQL page. Ordinary/default list shapes and
Persona input_request.reason are unchanged. BFF and FE can use this field for
the chair's specific requirement, and evidence_note for the actual report.

All former callers still serve distinct supported cases: separate note POST
for Persona/manual prepared evidence, reference-only submit/provide_input for
saved results, legacy ordinary RW-01/KW-02 CRUD, and list summaries for bounded
queues. No endpoint or historical record was deleted. The old reference-only
command allowlist branch was extended rather than creating an alternate writer.
`main.py` already forwards the original patch and authenticated context; no new
route or identity adapter is needed. BFF/FE forwarding and rendering remain with
BFF-MEETING-ACTION-HUMAN-INBOX-20261123 and FE-MEETING-ACTION-FOLLOWTHROUGH-20261123.

The real HTTP/PostgreSQL cases exercise direct human submit/return/correct/accept,
requested input and original Persona resumption, actual stored bodies and their
readback, denied/forged/foreign/stale/malformed requests with zero note writes,
six simultaneous exact retries, conflict without overwrite, and injected failure
after the real note insert followed by recovery. The read-only probe now verifies
body/source/identity equality against the original note owner and falsifies a
missing body. `inline-evidence-validation.txt` records terminal results. Existing
runtime caps and daily growth allocation remain in `storage.json`; the existing
notes writer manifest now names both POST and inline entrypoints. Lists never
load note bodies; details/probe resolve at most 16 distinct note records on demand.
The existing RC2/RC3/RC6 residuals below retain their original owners.

## Required-data integration correction (2026-11-23)

The operator reopened the existing task/PR to compose with P's specific-data
capability check. Before editing, confirmed the clean expected task branch at
9b251e1a1, fetched current dev 0c806ffed (already contained), read the canonical
task row and shared execution packet from PANTHEON_STATUS_ROOT, and traced the
existing create allowlist, assignment digest, list/detail projections, strict
HTTP ingress and PostgreSQL owner/CAS tests. Ordinary callers and historical
readers retain the distinct supported cases recorded above. Nothing is removed
or migrated for this additive optional field; no obsolete branch is introduced.

RW-01 accepts optional meeting_action.required_data: <=8 distinct nonempty
dataset identifiers <=256 characters, normalized array <=4096 encoded bytes.
The original assignment digest covers the field, including presence/absence and
order. Authorized original-ticket detail/list/transition responses preserve it;
old omitted-field tickets and their original digests remain unchanged. The
create assignment's existing 16 KiB cap still applies. No transition can edit
requirements, and this metadata is no grant of data access. M owns validation
and chair production of the same key, P owns actual data capability rechecks.
Source/test anchor: 6147b4be9. The existing inline human-note transaction and
historical response-recovery logic are unchanged and covered by regression.

Actual HTTP/PostgreSQL tests cover gated tw_price_daily/tw_broker_top metadata,
authorized list/detail reads, same-id retry after progress, conflicting dataset
changes/removal, foreign tenant/actor refusal, empty/max arrays, malformed and
oversized values with zero writes, unchanged omitted-field digest and ordinary
ticket creation. Terminal commands/results: required-data-validation.txt.
storage.json and the existing research_tickets manifest entry record the field
budget within existing row/input caps; no new write cadence or cleanup is needed.
Prior independent approval is superseded by this reopened scope; a fresh
Antigravity review of the exact PR/head is required. No merged, deployed or
hosted-accepted claim is made. Existing residuals below retain their owners.

## Residual findings and owning disposition

0. **Canonical delivery admission contention, existing development tooling /
   supervisor TaskStore maintenance owner (Gemini/fleet coordinator).** The
   governed handoff of PR #7760 at bead9792126c68844970b86778cfab15aedaa693
   remained in canonical preflight read_task_snapshot for 306 seconds, blocked
   acquiring the shared coordination-root/.orchestrator/task-state.lock. The
   worker interrupted only its own waiting process; terminal exit 130 and the
   traceback at common.py stable_sidecar_lock -> fcntl.flock confirm it had
   not reached mutation/admission. This is an observed infrastructure wait,
   not a diagnosis of the holder or authority to remove its lock. Keep the
   existing tooling owner responsible for lock reconciliation; no lock,
   command runtime or canonical JSON was edited. Source is committed/pushed;
   exact-head independent review is still required after admission recovers.
   See required-data-validation.txt for the separately collected blocker result.

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
