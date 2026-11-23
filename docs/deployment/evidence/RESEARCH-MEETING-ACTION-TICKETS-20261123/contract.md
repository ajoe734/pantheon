# Meeting-linked RW-01 ticket contract

Work in progress; component contract for the parallel meeting, Persona and BFF lanes.

The existing `/api/research/tickets` POST accepts the ordinary title, description,
priority, owner and an additional `meeting_action` object:

```json
{"meeting_id":"mtg-origin","source_action_id":"chair-action-1",
 "meeting_type":"MT-1","subject_ref":"pool-persona-1",
 "assignee_kind":"persona","deliverable":"Saved analysis",
 "acceptance_criterion":"Compare the stated alternatives with citations",
 "due_at":"2026-11-30T00:00:00Z","reviewer_id":"consultation-svc"}
```

`owner` is the accountable assignee. `next_review` (an explicitly identified
meeting) can replace `due_at`. Tenant is taken from the authenticated request.
The ticket ID is deterministically derived from tenant, meeting_id and
source_action_id; action_id equals ticket_id. A replay must match the original
assignment exactly. Ordinary tickets are unchanged and excluded from meeting queries.

Meeting ticket requests require bearer authentication and `X-Tenant-Id`.
Body actor_id, if supplied, must equal the verified principal. Read principals
are the assignee, assigned reviewer, required human, operator/admin, and the
tenant's existing `persona-pool-cycle` / `consultation-svc` service identities.
Only consultation-svc (service role) or operator/admin creates assignments.
The Persona controller may act for the accountable Persona, with both recorded.
Reviewer decisions require the assigned reviewer (with service/reviewer role)
or operator/admin; a generic ack/status patch is never acceptance.

PATCH uses `{"patch":{"action":"submit","expected_revision":1,
"idempotency_key":"run-result-1","evidence_refs":[{"kind":"research_artifact",
"artifact_id":"saved-output-id"}]}}`. Actions are start, request_input,
provide_input, submit, return, accept. request_input requires reason and
human_actor_id; provide_input requires evidence_refs or evidence_note and resumes the same owner.
return/accept require reason and review_source (the reviewing meeting ID).
Evidence is resolved again by Research through its existing tenant-scoped saved
artifact owner; references are metadata, never caller-asserted verified booleans.
Exact actor/key/payload retries are no-ops, conflicting retries or stale revisions
are 409. Submission stays open (`action_state=submitted`); only accept closes.

GET list with `meeting_linked=true` returns `{data,page_info}`; filters:
owner, meeting_type, subject_ref, action_state (comma separated), due_before,
submitted_after, updated_after, page_size (1..100), page_token. The stable keyset
uses ticket_id so updates do not reorder pending obligations. Follow every page;
limits must not silently discard unfinished work. Detail/summary expose the
meeting_action fields, action_state, revision, evidence_refs, required_actor and
action_id alongside ordinary RW-01 fields. Historical lists retain their list shape.

For the existing human inbox, use `meeting_linked=true&human_actionable=true`.
This optional filter selects visible rows whose `action_state=waiting_for_input`,
or whose `meeting_action.assignee_kind=human` and state is `open`, `in_progress`
or `returned`. It is ANDed with the original verified tenant/actor visibility
and all supplied owner/series/state/time filters in SQL before keyset/LIMIT.
Ordinary viewers see only their existing assignee/reviewer/required-actor rows;
operator/admin and existing tenant controllers retain their broader tenant view.
It does not expand access to another human's work or another tenant's records.
Omitting the flag or passing false preserves the previous query/cursor contract.
A cursor from the true-filtered page cannot be reused with the flag disabled,
or with a different authenticated identity or other filter scope.

Direct human assignments start `open`, with the human as accountable owner and
required_actor. The human can start/submit an existing authorized result; after
submit the required actor is the reviewer and the row leaves this queue. A
reviewer return restores `required_actor=owner` and makes it actionable again,
with its current revision. In contrast, `waiting_for_input` on a Persona's
ticket requires `provide_input`; that resumes the original Persona and leaves
the human queue. BFF must forward the verified human identity and expose the
appropriate transition from these original state/assignee/required_actor fields.
The filter is a read selection, not a grant to perform every visible transition.

This contract does not grant new tools or bypass capability, sponsor or trading
gates. Consultation owns verified origin/adoption; Persona owns safe execution;
BFF owns user identity propagation. Research owns ticket transitions and evidence.

For a durable analysis deliverable without a framework run, POST the existing
`/api/research/notes` with authenticated headers and
`{meeting_ticket_id, idempotency_key, title, body, source_refs?}`. Research stamps
an immutable tenant/ticket/actor binding; note_id is deterministic for ticket,
actor and key. Submission refs use `{kind:"research_note",artifact_id:note_id}`.
Ordinary note writes cannot forge or replace these bindings. Meeting notes are
excluded from the legacy unscoped note list and detail requires ticket access.
Every meaningful submitted claim still needs the reviewer's criterion check.

The original ticket PATCH also accepts an inline deliverable on `submit` or
`provide_input`, using the same authenticated principal and note owner:

```json
{"patch":{"action":"provide_input","expected_revision":1,
 "idempotency_key":"licensed-report-1",
 "evidence_note":{"title":"Report excerpt","body":"The actual supplied report content",
                  "source_refs":["licensed-report:page-3"]}}}
```

`evidence_note` accepts only title (nonempty, <=256 characters), body (nonempty,
<=12000 characters) and optional source_refs (array, <=8). The entire command
and the resulting immutable note must each fit 16 KiB encoded JSON. An inline
note may be combined with at most 7 ordinary evidence_refs; reference-only
clients retain the existing contract. Null or malformed notes are refused.
Actor, tenant, ticket, note identity and checksum are stamped by the owner,
never accepted from the inline object. Owner/role/state/revision and evidence
checks run before note creation. Note insertion and ticket CAS share one
PostgreSQL transaction on the locked original ticket. Failures roll back both;
simultaneous exact retries commit one note and one history event. A changed
payload with the same actor/key is refused and cannot overwrite the old body.
Review return uses a fresh key for a corrected report; both notes stay immutable.

PATCH and authorized GET detail resolve `evidence_notes` from the current
submission and human_input refs, with note_id, title, actual body, source_refs,
actor_id, tenant_id, ticket_id and checksum. Bodies stay only in the existing
notes table, outside repeated ticket history; list rows carry references only.
The original Persona and assigned reviewer can read the supplied input from
the original ticket. A required human's exact provide_input retry can recover
the response while that transition is still current, even though required_actor
has resumed the Persona. It does not grant later direct/list access; after a
subsequent transition, that historical retry returns 404 if the human no longer
has ticket access, preventing exposure of later owner results. It never writes
a second note or transition.

For `human_actionable=true`, direct human-assigned meeting rows also project
their existing bounded description, which carries the chair's specific human
requirement. Persona-input rows use input_request.reason. Default/false meeting
lists and ordinary ticket summaries retain their previous shapes. The filter
uses the same single bounded SQL page, without per-ticket HTTP reads.

Resource and interoperability details:

- Omit ticket_id on POST and use the returned ID. If computing it, the exact hash
  input is UTF-8 of `json.dumps([tenant_id, meeting_id, source_action_id],
  sort_keys=True, separators=(",", ":"), ensure_ascii=True)`; prefix `rt-action-`
  plus the first 32 hex SHA256 characters.
- At most 8 actions per tenant/origin meeting, 128 immutable evidence notes per
  ticket, 256 history entries and 256 KiB encoded ticket JSON. New assignments
  or evidence beyond capacity return 409. Existing rows and exact retries remain
  readable; no pending obligations are evicted. Note records and action commands
  are at most 16 KiB. Acceptance refuses changed or no-longer-resolvable evidence.
- submitted_after and updated_after boundaries are inclusive to avoid dropping
  events recorded in the same timestamp unit. Use revision/idempotency for dedup.
- Generic experiment create/retry continues for ordinary callers. It cannot
  mutate a meeting obligation's links or history; submit the genuine saved
  artifact through the explicit owner transition instead.
- The independent meeting/Persona tasks own the actual bounded prompt/reference
  envelopes. The chain manifest records their affected existing writers without
  claiming hosted or cross-component acceptance from this component's tests.
