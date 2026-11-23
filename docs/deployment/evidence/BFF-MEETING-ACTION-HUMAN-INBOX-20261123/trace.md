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
  A paged owner operation serves meeting-linked production HTTP requests; legacy
  list remains for ordinary RW-01 callers, whose non-meeting history and local
  pagination contract are unchanged. The unfiltered TypeError retry is removed.
* Regression starting points: `tests/test_meetings_views.py`,
  `tests/test_meetings_human_inbox.py`, `test_rw01_research_ticket_contract.py`.

Physical storage-manifest edits belong solely to the Research lane per shared
packet. BFF adds no persistent writer, cache, recurring job or dispatcher.
Contract HTTP doubles prove component behavior only. Real-owner integrated
and hosted proof remain with the chain acceptance owner.

Privacy-review resumption: inspected the canonical rejection on PR #7761 and
the authenticated meetings caller, existing round citation filter and
`_extract_item_ref_ids` visibility helper before editing. Refreshed from
current dev `ba7c32e07` by merging its unrelated distillation changes without
rewriting the published task history. The repair reuses the existing helper
to drop whole withheld citation mappings and normalizes scalar ID whitespace.
All historical callers listed above retain their distinct purposes; no new
caller or obsolete route is introduced by this correction.

Source-isolation resumption: inspected current dev `0c806ffed`, canonical PR
#7761 rejection at `21644e8b5`, `ManagementService.get_human_inbox`, its
contributor registration, `read_human_inbox_meeting_items` and the authenticated
inbox fixture before editing. Registration already recognizes independent
Research configuration; only the earlier store/Consultation-specific guard
prevented that reader from running. Replace that duplicate decision with an
empty-reader check, preserving the existing no-source response. Research
remains the ticket owner; no new adapter, route or storage is introduced.

Typed-reference/partial-page resumption: inspected current dev `0c806ffed`,
canonical rejection at `99ca1187a`, `_shape_subject_ref`, recursive action
visibility and the existing inbox contributor aggregate before editing.
Typed trader/agora_user mappings now reuse ordinary subject-reference
redaction at every action depth. Other trader records retain their existing
visibility rule. A readable paginated owner counts as partial availability.
All callers above remain supported; this adds no owner call or stored state.

Direct-human resumption: before editing, inspected current dev `0c806ffed`,
PR #7761 head `323ac0cf336a`, the original contributor and ticket list/PATCH
callers, and Research's canonical scope correction plus owner implementation.
The obsolete waiting-for-input-only selection is replaced by the existing
owner's `human_actionable=true` union before pagination. The original paged
ticket facade forwards that filter and cursor; explicit action_state queries
remain supported for their distinct existing consumers. No route or history
is removed. The original PATCH/receipt already forwards both submit and
provide_input, so it needs no second command handler or BFF transition logic.
