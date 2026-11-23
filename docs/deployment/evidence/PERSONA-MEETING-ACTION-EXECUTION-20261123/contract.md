# Persona meeting action consumer

Task: PERSONA-MEETING-ACTION-EXECUTION-20261123. RC2 WP2.3 / RC6 meetings.
This replaces the design-only anchor with the implemented contract. Local
component validation does not claim hosted acceptance.

## Owner and caller trace

The initial owner traced bd47d73bc. This continuation read the anchor, rebased
on dev ba7c32e07 and read the committed Research contract at approved head
cddd6188f (PR #7760). The old Research worktree was gone; git preserved its contract.

* PersistentPersonaOwner owns registry/tenant/lifecycle and the active harness;
  PersistentCapabilitySnapshotOwner owns immutable access receipts.
  PersonaPolicyResolver resolves referenced catalogs and denies unknown IDs.
* PersonaCycleController.consume_run remains the cron consumer with its existing
  cross-process lock/claim. CycleOwners remains the owner port. Citation and
  broker-record gates precede action execution. The plugin still exposes three
  brokered tools. panel_interpretation remains recall-only.
* Research owns original tickets, revisions, actor checks, notes and review states.
  Consultation owns adoption, public frozen cases and acceptance/return.
* CaseFileOutcomes still measures cited lessons; MeetingTriggers still convenes;
  ordinary demand/admission/risk readers retain distinct supported callers.
  Nothing replaced their final caller, so no obsolete branch was removed.
  Historical records and JSONL legacy/test transport remain readable. No data,
  position, capital or private-bank migration occurs.

## Capability

GET /api/personas/{persona_id}/action-capability requires authenticated tenant
scope. Optional run_purpose defaults to daily_cycle; required_data may repeat.
The meeting producer's GET caller matches. The design anchor's speculative batch
POST was not implemented because no caller needs it.

The evaluator joins fresh owner Persona/snapshot, the verified active harness,
broker tool ceiling and any referenced policy resolver. Unknown policy IDs fail
closed. Inline BFF snapshots and chair/trading labels grant nothing. The receipt
must grant research_note, persona_proposal_submit and a daily-cycle workflow;
the harness must retain all five steps. Its established read permission permits
the already brokered tools; read alone cannot replace a missing snapshot grant.
A research_note based only on authorized same-ticket evidence executes
independently of unrelated market feed ingestion gates (such as tw_price_daily
and tw_broker_top). A deliverable requiring market/licensed data must verify
authoritative snapshot metadata.accessible_data, non-seed source class,
connector/schedule/health/purpose policy gates and explicit revocations, or
surface the exact missing_data. Expiry,
revocation, suspension, restrictions, missing workflows and owner faults return
named denials. Consultation runs cannot execute; trading mutation is always false.
Execution repeats this same evaluator against both fresh owners. No missing
policy catalog, access receipt or enrollment is fabricated.

## Existing-cycle execution

Each normal eligible cycle reads up to three bounded Research pages (page size four):
open/in_progress/returned filtered by accountable owner, and submitted/accepted
queried by M's actual stable MT-1 pool series (subject_ref=pool:<persona_id>) plus
genuine supported historical/persona series (subject_ref=persona:<persona_id>,
owner=<persona_id>) when page capacity remains.
next_page_token persists in existing packets and continues next cycle, resetting
after exhaustion. pending_pages is explicit; owner/evidence failure retains the
cursor. This avoids permanent first-page truncation. Runnable rows require exact
tenant, accountable Persona owner, action_id and persona assignee kind. Verified
result rows align with M's pool:<persona_id> series (and genuine persona:<persona_id>
MT-1/MT-2/MT-4 series), admit approved same-subject shared human/other-assignee
research without treating its assignee as the deciding Persona, and explicitly label
submitted versus accepted (state, review_status, accepted, review_decision, owner,
assignee, assignee_kind, accountable_persona_id).

Chair source_refs are actual citation strings. They resolve from the original
tenant-scoped public meeting case with origin/digest/content/sharing checks.
Private memory is refused. Human/result reference objects resolve through scoped
Research note/artifact owners and binding/checksum checks. No caller URL is fetched.
Observe carries full deliverable/criterion plus bounded approved excerpts. The case
gains facts:meeting_action_ticket:<ticket_id> and facts:meeting_action_result:<ticket_id>
anchors, independent of semantic memory. A later no_action may cite the result
and explicitly explain keeping course.

The existing persona_proposal_submit accepts kind=meeting_action. Parameters:

* ticket_id, action=submit, title, analysis, evidence_refs (approved objects only);
* ticket_id, action=request_input, missing_requirement, human_actor_id;
* ticket_id, action=decline; the reason is recorded and the ticket stays open.

A ticket citation is required. At most four distinct tickets execute per tick.
The exact validated commands are journaled in the existing packet before writes.
Research notes retain actual Persona, packet and broker-run provenance. Note IDs
use Research's published tenant/ticket/controller/key hash; GET before POST reuses
existing evidence. PATCH sends patch.action, expected_revision and idempotency_key.
Note identity/actor/content and ticket receipts must match. Timeout is unavailable,
never submitted. Submission stays reviewable; Persona never confers acceptance.

The next successful ordinary tick replays up to four uncertain saved plans before
new observations. Completed recovery is remembered in existing packets. An uncertain
ticket cannot get a new generated execution. The existing lock fences concurrent
ticks, and a claimed-run restart never buys that original model turn again.
Research's authorized provide_input resumes the original accountable Persona and
ticket; the same normal cycle then observes and submits it.

## Resources supplied to the Research/storage owner

No new cadence, collection, queue or inbox. Existing packet, note and ticket writers
are affected. Three enrolled Personas normally produce three packets/trading weekday,
15/week; separately human-convened meetings remain counted by their chain. Three
meetings/persona/week with eight actions each implies a theoretical 72 assignments/week.
Four actions per Persona tick permit 60/week at weekday cadence: sustained maximum
arrivals can outpace consumption. Open obligations remain preserved and paged.

* Up to three list GETs plus <=64 evidence GETs/tick, caching meeting reads by origin.
  Four recoveries + four new executions need <=4 owner calls each (ticket GET,
  note GET, optional note POST, PATCH): <=99 HTTP calls/tick, <=297 across three
  Personas. Each times out after 10 seconds, with <=1 MiB response reads. No
  recursive fetch or in-tick retry. Capability uses <=8 pairs of local owner reads.
* Observe extension <=192 KiB; case anchors <=64 KiB/case; actual whole turn input
  <=512 KiB UTF-8, checked before broker registration. Analysis <=8,000 UTF-8 bytes;
  note command <=14,000 JSON bytes leaves space under Research's 16-KiB record cap.
  At most four journal plans/tick and twenty proposal cases plus the cycle case.
* Hosted JSON retains one snapshot/packet, with <=10 writes/tick (claim, two per new
  action, terminal). Existing write-time cleanup: 30 days / 1,000 shared records and
  run watermarks. Legacy JSONL is unchanged; JSON retention is not claimed for it.
  Added fields have a conservative <=1.6 MiB/packet envelope (21*64-KiB anchors,
  192-KiB Observe, four <=16-KiB plans), plus pre-existing packet content. At three
  new packets/day, additional growth <=4.8 MiB/day at that ceiling; normally <=90
  daily Persona packets/30 days, fewer under weekday cadence, within the shared cap.
* measurement.json records the reproducible four-action component fixture: real
  turn bytes, hosted-format packet/store bytes, request count and note count/bytes.
  These are local test measurements. Daily example growth is three times the
  measured full packet/store and note bytes, not a hosted measurement.
* Research retains 128 notes/ticket, 256 history entries/ticket, 256-KiB tickets and
  eight actions/origin. Its existing RC3 retention residual is not changed here.

## Acceptance boundary

probe.py is executable read-only acceptance: real original ticket/note, actual
broker run, owner acknowledgement and a later same-Persona decision citation from
a copied hosted packet store. --help lists origins, Persona, tenant, ticket and
packet-file arguments. Credentials use existing token environment/file variables.
No ticket, note, cron or task mutation occurs. The two-meeting proof task and
MEETING-HOSTED-ACCEPTANCE-20261115 must run against the exact deployed pair and
retain real human-input and later-meeting proof. Fixtures do not satisfy that gate.
