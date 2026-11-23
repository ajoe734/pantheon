# MEETING-ACTION-TWO-MEETING-PROOF-20261123 handoff

Three separate verdicts. None is hosted or true acceptance.

## 1. Component merge (this branch)
- `acceptance/meeting_action_followthrough.py` + `acceptance/probes/meeting-action-followthrough` (read-only, GET only).
  `acceptance/test_meeting_action_followthrough.py`: 17 passed (valid chain; same meeting twice; legacy W47; nonexistent
  evidence; checksum drift; inbox-ack and generic-demand evidence; wrong tenant/series/meeting; self-verification;
  missing ticket; series differing across meetings; missing opportunity; submitted work not presented/answered;
  pending is never a pass; probe exits 77 and leaks no secret).
- `services/consultation/tests/test_persona_meeting_action_loop.py`: real Research HTTP owner + Postgres via
  consultation's normal dispatch/read entry points (idempotent restart, no duplicate POST, series/tenant scoping,
  owner evidence read). **Not executed locally**: no isolated Postgres was available (starting one via docker was not
  permitted in this session), so it skipped. It must run where `RESEARCH_MEETING_TEST_DSN`/CI `DATABASE_URL` exists.
- Anchor commits also deliver the `consultation-svc` research principal (issuer + compose); 100 issuer tests passed.

## 2. Hosted pair: PENDING (not observed)
Not recorded: FE/BFF release SHAs, real M1/M2 ids, action/ticket/receipt/citation ids, sanitized browser paths for
minutes and inbox, evidence verdict. Reason: no natural second same-series meeting nor authorized human input has been
observed on dev from this worker. Run on dev when they exist:
`ACCEPTANCE_MEETING_ACTION_M1=<id> ACCEPTANCE_MEETING_ACTION_M2=<id> acceptance/probes/meeting-action-followthrough`
and hand the JSON plus UI paths to MEETING-HOSTED-ACCEPTANCE-20261115 (existing owner; no new gate/job/prod write).

## 3. True acceptance: NOT accepted
Remaining: natural M2, real authorized human input with same-Persona resumption, UI walk (meeting -> work item ->
result -> next meeting), W47 as historical context only, denial for unrelated viewers, no private-memory leakage.

## Caller/obsolete-path trace
No product code replaced and nothing deleted: this task only adds acceptance code and tests. Existing
`meeting-review-disposition` and `persona_day` are untouched; they serve distinct cases (REVIEW dispositions, daily
persona cycle). Residual defects found: none in owners; stale-writer/route leftover search belongs to a post-merge
`git grep` by the owning implementation tasks once the hosted pair exists.
