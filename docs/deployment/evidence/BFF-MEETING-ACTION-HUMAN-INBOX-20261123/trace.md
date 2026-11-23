# BFF meeting action caller trace

## Required smoke-gate repair (2026-11-23)

Resumed published PR #7761 at `ba53473eb`; merged current dev
`454420828361de17e689e6d9ad307e12b4064d3c` without rewriting published commits.
Before editing, traced the existing ticket route, ResearchRouterService,
ResearchPortWiring, ReadSurfacePorts, DefaultResearchKnowledgeSourcePort and
ResearchServiceClient. The meeting contributor calls that same HTTP client
directly with `human_actionable=True, page_size=100`. Research task head
`f6b32132725a08aa8bf382a722334a2bea40436d` exposes the same existing owner list
endpoint and bounded filters; it remains the storage and authorization owner.

Removed generic `**filters` signatures from this one page query at all four
adapter/wiring layers and the generic meeting-filter argument on its existing
service caller. Explicit parameters cover status, owner, meeting type, subject,
action state, due/submitted/updated cutoffs, human-actionable selection, cursor
and page size. The existing route's enumerated filter extraction remains its
supported HTTP input case. The ordinary unlinked RW-01 list remains separate
because it serves historical non-meeting tickets. No route, query, alternate
store or writer was added; historical records are untouched. Architecture
guards and baselines are unchanged.

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

Caller-permission resumption: before editing, inspected clean PR #7761 head
`17381897f`, current dev `0c806ffed`, the original inbox router's authenticated
identity, ManagementService contributor context, original ticket PATCH's
`ResearchRouteContext.identity(operator=True)` and canonical
`auth.policy.require_operator_role`. Compared with Research owner
`b99655d779b0` `_patch_meeting_ticket` actor/state checks. The existing caller
identity now reaches that same contributor; `can_respond` is projected on
the original meeting_action row. No extra owner read, authentication mechanism,
writer or endpoint is added. Legacy REVIEW/refusal/proposal consumers and all
ticket writes retain their supported purposes and authorization boundaries.

Legacy-fixture resumption (2026-11-23): task-scoped canonical contract now owns
`acceptance/test_meeting_review_disposition.py`. Reproduced its isolated
round-trip failure at report_before after merging dev `2df217b50` (owner PR
#7766) into the clean published task branch. Traced the fixture's build_bff_app,
ManagementService contributor, request ContextVars, verified_caller_tenant,
ResearchServiceClient and default urllib transport. The fixture omitted
OwnerReadContextMiddleware and the Research owner configuration/response, so
the current honest source status correctly failed the unchanged probe.

Anchor `978a51aa0` adds the existing middleware and configures one local
Research HTTP host in that fixture only. The real Research client issues the
filtered authorized GET; its local transport verifies viewer Bearer, tenant,
meeting_linked=true, human_actionable=true and page_size=100, returning
data=[] with page_info. Two probe reads and one reload require exactly three
Research calls. All original assertions, the executable probe, production
authorization/degraded behavior and architecture guards remain unchanged.
No old caller was removed: legacy REVIEW acknowledgement remains a supported
independent case and cannot deliver action work. Existing residual findings
retain their owners; the archive scans remain visible in the merged
Consultation list readers. No product writer, cache, job or storage change.
