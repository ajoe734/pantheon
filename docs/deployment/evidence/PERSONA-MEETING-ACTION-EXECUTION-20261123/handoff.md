# Persona consumer handoff

RC2 WP2.3 / RC6 meeting follow-through. Source rework is published on PR #7765;
governed review admission is blocked by inherited commit provenance (details below).
No merge, hosted deployment or acceptance is asserted. Owner Codex, assigned reviewer Codex2.

The implemented interfaces and existing caller/owner trace are in contract.md.
Research's approved #7760 contract, including original action_id=ticket_id, nested
PATCH commands, immutable note identity and next_page_token, is the consumed shape.
Consultation's current GET action-capability caller matches this consumer. Its
string source citations now resolve frozen public case contents rather than being
misinterpreted as Research artifact objects. No historical action backfill occurs.

Actual execution records both the accountable Persona and the broker run submitting
the result. A fetched ticket, model text, declined proposal, unrecorded proposal or
timeout never becomes a submission. The read-only probe requires owner evidence,
acknowledgement and an actual later decision citation. Consultation remains advisory.

The tests use the existing real Persona owners, cron/adapter contract, broker-record
and citation path, plus an HTTP Research/meeting contract stub. This is component
validation; the Research lane separately owns real PostgreSQL transition/auth tests.
The final two-meeting task must use real owners, authorized human input and two
different same-series meetings on the exact hosted pair. A fixture is not that proof.

## Resource handoff

measurement.json is the reproducible four-action normal-tick measurement. Scale it
by three for a trading-day example; no additional job/cadence was introduced.
contract.md states HTTP counts, bytes, worst-case ceilings, retained record count,
daily growth and existing cleanup. The existing storage-manifest entries are owned
physically by RESEARCH-MEETING-ACTION-TICKETS-20261123 for this chain; Persona has
not concurrently edited the manifest. Supply these figures to that owner before
integrated acceptance. Source acceptance must not label fixture sizes as hosted.

## Residual findings and owners

* Current canonical provenance blocker / Human/Ops or coordinator delivery owner:
  governed handoff of tested head 9ed177a45947d5cd13c580767038912685fb7fe4 exited 1.
  The runtime rejected inherited commit df9e0209c59799ff080aa9a23c79c8e97414d392:
  `LLM-Agent: Antigravity2` does not match current owner Codex and no audited
  task_reassigned chain following commit time 2026-11-23T06:07:55+00:00 explains it.
  The gate requests amending that trailer to Codex. This ancestor is published;
  no authorization to rewrite published history, alter original attribution or
  fabricate reassignment was supplied. Preserve genuine provenance. Human/Ops or
  the coordinator must resolve the audit/history authority before a fresh handoff.
  No admission, reviewer approval, merge or acceptance is claimed. Do not retry
  the same failed gate without new corrective authority/evidence.

* Historical canonical provenance / coordinator delivery finding: handoff of ff454c28a was
  rejected (exit 1). Inherited contract commit 364a1f1bd retains its genuine
  LLM-Agent: Antigravity2 and author time 2026-11-23T04:12:53Z. Rebase set its
  committer time to 2026-11-23T04:56:18Z, after Codex inherited this task. The
  command runtime's reject_owner_trailer_mismatch_at_handoff reads %cI and found
  no subsequent audited reassignment, then requested amending the inherited
  trailer to Codex. The branch is already published; a normal non-force push
  cannot replace that ancestor. No history rewrite, forged reassignment or guard
  edit was attempted. The coordinator must disposition historical provenance or
  authorize the narrow published-history repair before a fresh exact-head handoff.
  The worktree is preserved in ordinary commits; approval/merge is not claimed.
  This is the prior handoff result, not evidence of the new head's admission.
  Current governed admission must determine whether this hold still applies.

* RC2 WP2.3, Persona enrollment / two-meeting proof owner: hosted access provisioning
  remains unverified here. The three trading Personas need genuine owner-issued
  research_note, decision-tool and daily-workflow receipts, fresh harnesses and
  resolvable policy references. Missing/expired receipts fail closed. No snapshot or
  role-based permission was manufactured to turn this prerequisite green.
* RC2 WP2.3 / RC3 WP3.3, existing storage and loop owners: exact uncertain-write replay
  uses retained packets. After the shared 30-day/1,000-record policy prunes the original
  plan, Research's original ticket/note still exists, but the consumer will not invent
  the lost original proposal/content. The chain must assess prolonged outage recovery
  and the worst-case 72 arrivals versus 60 executions/week; outstanding obligations
  remain visible at Research. This is a retention/capacity limit, not a silent success.
* RC3 WP3.1/3.3, Research storage owner: existing overall Research retention remains
  the upstream residual recorded by #7760. Per-ticket history/note limits and bounded
  Persona envelopes do not prove a whole-database retention policy.
* Review independence exception: canonical assignment remains Codex2, the same family
  as owner Codex. The current command-runtime supervisor capacity lookup and actual
  agent_can_take_task results are recorded in review-capacity.json. All configured
  alternatives checked there are unavailable; preserve the assigned reviewer under
  this capacity exception. This observation is not a review or approval.

No new remediation task, global task edit, workflow gate, credential, deployment or
second dispatcher was introduced. The transient status-outbox refusal at wake-up
cleared through existing governed recovery; show/progress subsequently succeeded.

## Review-rejection remediation (Antigravity2)

* Fenced every unresolved durable ticket plan via `meeting_action_deferred`
  (independent of the four-entry replay batch); regression: pending plans beyond batch.
* Per-row source isolation: a ticket with an unresolvable approved source stays visible
  with `source_unavailable` so `request_input` works; `submit` is denied with the exact
  reason. Regression: missing-source original-ticket human request.
* Validation: review regressions 2 passed; focused suite 612 passed/9 skipped/1 failed.
  The failure (`test_submit_next_same_subject...`, probe `created_at` ordering) passed
  4/4 alone and on baseline; suspected same-second `created_at` tie flake. Residual for
  the Persona test owner. Plugin npm test and diff budget not rerun.

## Correction round 3 (head after e0fff8407)

- Capability: a paper_advisory receipt (persona_opinion only, no tools/workflows) no longer caps the
  daily cycle. For run_purpose=daily_cycle the ceiling derives from the broker profile, active
  five-step harness, owner policy resolver and data availability; explicit restrictions, expiry,
  downgrade, unverified authority and consultation purpose still deny. get_for_persona(run_purpose=
  daily_cycle) prefers explicit non-advisory receipts so a later advisory receipt cannot shadow a
  daily denial/grant. Same check at assignment and execution. No backfilled grants.
- Evidence: title/id/checksum-only artifact projection raises evidence_content_missing, so the ticket
  keeps requesting the genuine missing input; same-ticket note bodies remain consumable.
## Correction round 4 (Antigravity)

- Deliverable capability semantics: a `research_note` based only on authorized same-ticket
  evidence executes independently of unrelated market feed ingestion gates (`tw_price_daily`
  and `tw_broker_top` on the three real enrolled dev Personas), without fabricating
  `accessible_data` or disabling data gates. Any deliverable requiring market/licensed data
  (`required_data` or `requires_market_data`) verifies authoritative `accessible_data`,
  non-seed `source_class`, connector/schedule/health/purpose policy gates and explicit
  revocations, or denies with `restricted_data` and surfaces `missing_data`.
- Decision result lookup: aligned `observe_meeting_actions` with M's normalized MT-1 series
  (`subject_ref=pool:<persona_id>`) via R's authorized bounded series query, while preserving
  genuine historical/persona series (`subject_ref=persona:<persona_id>`, `MT-1`/`MT-2`/`MT-4`).
  Only accountable Persona items enter `runnable`. Approved same-subject shared human and peer
  research enters `verified_results` with actual `owner`/`assignee`/`assignee_kind` and
  `accountable_persona_id=None`, accurately labeling `submitted` vs `accepted` in both Observe
  and CaseFile `facts:meeting_action_result:<ticket_id>` items even with zero semantic hits.
- Clock-tie guard in `probe.py`: uses `(created_at, cron_run_at_ms)` ordering across distinct
  packet IDs so same-second test executions cannot flake.

## Correction round 5 (Antigravity)

- Independent human replacement resolution: `observe_meeting_actions` prioritizes and
  independently resolves same-ticket `human_input.evidence_refs` before `meeting_action.source_refs`
  within the 8-reference per-ticket budget so an immutable previously missing chair `source_ref`
  never short-circuits a readable authorized human replacement note.
- Preserved missing origin provenance: the original missing reference and its recorded reason
  (`meeting_source_not_resolved`, etc.) are preserved in `unavailable`, `missing_sources`,
  `original_source_unavailable`, CaseFile `facts:meeting_action_ticket:<ticket_id>`
  `subject_fields`, and the journaled execution plan, while `meeting_action_plan` allows
  `submit` only when the resolved human replacement is explicitly cited in `evidence_refs`.
- Fail-closed boundaries: no bypass for real denied datasets (`tw_price_daily`/`tw_broker_top`
  with `accessible_data=[]`), foreign tenants/tickets, private notes, persistent owner 5xx
  outages, empty `human_input`, or title-only references.

## Correction round 6 (Codex)

- Reproduced the coordinator's remaining incident on 716e3efb2 with a single accepted
  operator-owned MT-2 ticket for persona:<deciding Persona>. The ordinary consume_run
  produced zero verified_results before the fix (new positive regression failed).
- Removed the obsolete owner prefilter on the existing bounded persona-subject result
  query. The existing Python series check now admits human MT-2 work for that exact
  subject, requiring independent accept review; normal owner-bound runnable admission
  and Persona-owned historical series stay distinct. No route/service/store/job was added.
- The ordinary decision reads the actual same-ticket note body and cites
  facts:meeting_action_result:<ticket_id> with zero semantic hits. Observe and CaseFile
  label real human assignee and reviewer separately, accountable_persona_id=None.
- Sixteen normal-cycle negative cases cover other Persona subjects, unsupported MT-3,
  peer-Persona assignees on a persona series, human runnable attempts, submitted or
  missing/self/returned/malformed acceptance, cross-tenant or wrong-ticket evidence,
  private evidence, changed checksum and title-only content. Suppressed series never
  cause an evidence-owner lookup. Prior missing-origin human-resume regressions pass.
- Resource limits/cadence/count/cleanup in contract.md remain unchanged: same three
  bounded list queries, four results/tick, eight refs/result, existing envelope caps.
  Reviewer labels fit those enforced caps; no new persistent runtime writer exists.
- Current caller/storage trace: this correction changes only CycleOwners' existing
  Research reader and _action_case_file's existing fact projection. Research remains
  ticket/note/review owner, Consultation remains meeting-series producer. Prior owner
  prefilter was the rejected obsolete restriction; no historical records were removed.
- Validation and exact runtime blobs for the new review head are in evidence.json.
  Existing executable probe.py checks Persona-issued submission plus later citation;
  the hosted two-meeting proof owner must additionally prove this human MT-2 branch
  with real accepted owner records. No hosted acceptance is inferred from HTTP stubs.
