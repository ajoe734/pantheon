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
human_actor_id; provide_input requires evidence_refs and resumes the same owner.
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

This contract does not grant new tools or bypass capability, sponsor or trading
gates. Consultation owns verified origin/adoption; Persona owns safe execution;
BFF owns user identity propagation. Research owns ticket transitions and evidence.
