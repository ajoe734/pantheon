# BFF meeting action caller trace

Source inspected before edits: `bd47d73bc` (task branch from dev).
Class: RC2 WP2.3, cross-reference RC6 meetings, existing
`MEETING-HOSTED-ACCEPTANCE-20261115`; execution contract is the
`meeting-action-followthrough-20261123` packet in the canonical status root.

* Management/Agora meeting routes call `MeetingsOwnerClient.get_meeting`, then
  `shape_meeting_detail`. Consultation owns minutes in its existing store.
  Its detail must supply frozen `actions`, `prior_actions`, safe participant
  capabilities and separately labelled live owner reads. BFF never backfills.
* Management human inbox calls the existing meetings contributor through
  `ManagementService.human_inbox_contributor_readers`. REVIEW dispositions,
  participant refusals and pending proposals remain distinct supported cases.
  The contributor will also read one filtered Research ticket page; no
  meeting/archive scan or per-ticket hydration will be added.
* `/api/v1/research/tickets/{id}` PATCH calls ResearchRouterService, the
  read-surface/knowledge adapter and ResearchServiceClient, finally the
  existing ResearchWriteOwner PATCH/store. Existing BFF patch validation drops
  action-specific inputs and returns only four fields. The owner must own
  revision checks, replay and transitions; BFF must preserve its receipt.
* Existing ticket list materializes an owner list before local page slicing.
  A paged owner operation will serve normal production HTTP requests; legacy
  list remains for separately supported ordinary in-process/test callers.
* Regression starting points: `tests/test_meetings_views.py`,
  `tests/test_meetings_human_inbox.py`, `test_rw01_research_ticket_contract.py`.

Physical storage-manifest edits belong solely to the Research lane per shared
packet. BFF adds no persistent writer, cache, recurring job or dispatcher.
Contract HTTP doubles prove component behavior only. Real-owner integrated
and hosted proof remain with the chain acceptance owner.
