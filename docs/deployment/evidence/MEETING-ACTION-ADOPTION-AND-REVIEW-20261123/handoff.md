# Meeting action adoption and review

Owner Codex2; assigned reviewer Codex. In progress, no approval or hosted acceptance.

Baseline inspected: ba7c32e071a5d357067afc08addd78736aa25fd8, current origin/dev.
The recovery brief describes an earlier draft but neither this clean task branch,
its reflog nor visible task commits contain that draft. Implementation is rebuilt
against the current source and committed Research/Persona contracts.

Pre-edit trace: main.py persona meeting routes -> convene_persona_meeting ->
ConsultationStore.put_meeting / backing ConsultRequest -> workflow_executor ->
execute_persona_meeting -> provider.obtain -> adapter's normal meeting round and
synthesis schema -> validate_and_build_synthesis -> dispatch_proposals -> existing
owner endpoints -> memos / learn-feedback writebacks -> detail view.
Existing case_file composer resolves Registry member facts; use that path unchanged.
Current participants' private pre-read remains per-persona and is never shared.
Tests: test_persona_meetings, test_persona_meeting_routes, test_meeting_outputs,
adapter test_consultation_meeting_rounds.

ResearchWriteOwner (RESEARCH-MEETING-ACTION-TICKETS-20261123) owns ticket identity,
transitions, evidence validation and progress. No ticket hash here. The existing
dispatch ledger stores frozen requests and valid receipts only. Persona's
/api/personas/{id}/action-capability owns capability; no role-based grant here.
Existing research_demand remains supported for pool discovery, not document work.
Formal proposal/sponsor and paper-only gates retain their independent purpose.
No new persistent collection, cron, or inbox. R owns chain storage-manifest edits.

Pending implementation: integrate helpers into convene/execute/readback and actual
adapter schema; regression tests and resource measurements; exact-head PR handoff.

Residual findings for coordinator:
- RC6 meetings/BFF projection owner: legacy list_meetings_view and
  list_meeting_proposals_view scan the meeting archive. New action reads query
  filtered owner pages and direct ticket links; this task does not duplicate them.
- RC3 storage retention owner: existing meeting archive and dispatch ledger have
  no whole-history cap/cleanup. Reference/record limits do not imply archive caps.
- RC2 WP2.3 coordinator: recovery source did not contain the advisory draft;
  no previous validation or acceptance is inherited.
