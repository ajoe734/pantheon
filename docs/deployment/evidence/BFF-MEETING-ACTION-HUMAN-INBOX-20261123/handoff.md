# BFF source review handoff

## Earlier repairs (condensed; exact commands and hashes in evidence.json)

- Legacy REVIEW fixture repair (`978a51aa0`): only
  `acceptance/test_meeting_review_disposition.py` changed; it installs
  `OwnerReadContextMiddleware` and a local Research HTTP endpoint, asserts
  Bearer/tenant forwarding and `meeting_linked=true`, `human_actionable=true`,
  `page_size=100`, three owner GETs. Acceptance file passed (4 tests, exit 0);
  eleven-file regression passed 384 tests (exit 0).
- Architecture-gate repair (`3ad5bb5a9`, `8981dc0ef`): the generic `**filters`
  signature in `ResearchPortWiring.list_research_ticket_page` was replaced by
  the bounded owner list parameters; filters verified through the authenticated
  route and owner HTTP boundary. Regression passed 384 tests (exit 0);
  Consultation producer composition passed 17 tests (exit 0). Hosted probe
  exited 77 (configuration missing): no hosted acceptance is claimed.
- Runtime budget unchanged: on-demand, one Research page of at most 100 rows
  per inbox read, 4 MiB owner reply cap, no BFF writer/cache/recurring job,
  zero daily storage growth, request memory released after response.

Owner: Codex2. Reviewer: Antigravity (current canonical reassignment). Delivery target: `dev`.
This packet proves component behavior against owner HTTP contracts. It does
not prove merged, deployed, accepted, or integrated two-meeting execution.

## Normal callers and owner contract

* `GET /bff/management/meetings/{id}` keeps consultation-owned `actions`,
  `prior_actions`, `participant_capabilities`, `prior_action_reviews`,
  `series_subject_ref`, `action_read` and `action_page_info`. Frozen minutes
  and separate live owner fields remain separate; BFF does not infer delivery.
  Action subtrees apply tenant, trader and private-memory visibility, including
  references to withheld case-file items. Owner denial returns 403; transport
  failure remains an explicit unavailable envelope; legacy minutes get empty
  lists and `meeting_actions_not_recorded`. No historical record is modified.
  The meeting producer owns supplying live owner status and capabilities.
* Existing `GET /bff/management/human-inbox` meetings contributor reads one
  authorized `meeting_linked=true&human_actionable=true&page_size=100`
  Research owner page. Each original ticket produces one `meeting_action`
  entry, linked to the same meeting/action and original ticket facade. It
  carries the direct-human requirement or Persona input reason, required material, original owner, actor required now,
  resume owner, revision, due/review, overdue and original dates. A REVIEW
  disposition remains its own item and cannot deliver or accept ticket work.
* Existing `GET /api/v1/research/tickets?meeting_linked=true` forwards owner,
  meeting_type, stable subject_ref, action_state, date filters, page size and
  opaque page token. Owner `data` and `page_info` are preserved. The inbox
  surface exposes the Research continuation and facade link; a nonempty owner
  cursor marks the aggregate partial, so its local total is not an archive
  census. Continue via the ticket facade; no archive walk or ticket fanout.
* Existing ticket PATCH forwards the authenticated caller and verified tenant
  to Research. `action=submit` or `provide_input`, original `expected_revision`,
  `idempotency_key`, `evidence_refs` (owner-issued kind/artifact_id) and any
  optional `evidence_note={title,body,source_refs?}` and allowed review command
  are validated by the owner. An Idempotency-Key header
  supplies the body key; conflicting keys fail. PATCH requires an operator
  route role, and the owner checks its own finer role/actor/tenant/state rules.
  It returns the full durable owner receipt, human_input and history. Stale
  revisions and denial retain 409/403; no BFF replay cache exists.

Actual aggregation is in `management_read_models/service.py`, rather than the
brief's `governance/human_inbox.py`. The composition changes there
keeps Research work readable when consultation is unconfigured and marks
partially available contributors as partial without discarding their rows.

## Old callers, removals and history

Retain REVIEW, refused/due meeting and pending formal-proposal projections:
each has a distinct supported purpose, and none is a research action receipt.
Ordinary non-meeting RW-01 list and PATCH validation remain available; their
historical rows are not enrolled in the meeting queue. Remove the TypeError
fallback that retried an unfiltered ticket list. Meeting-linked responses now
bypass the ordinary four-field PATCH response and local ticket cache; ordinary
ticket projections still serve their existing callers. No unused compatibility
route, new work store, scheduler, inbox, backfill or data migration is added.

## Resource acceptance

See `resources.json` for reproducible simulated inputs and measured bytes:
100 distinct tickets (50 Persona input requests and 50 returned human
assignments) with 2000-character deliverable, criterion, input/return reason
and direct-human requirement: owner JSON 772715 bytes, inbox rows 1302250
bytes. `measure_resources.py` prints these counts/bytes without writing files;
this is synthetic ASCII input, not a maximum UTF-8 envelope or hosted data.
Research HTTP success/error bodies have a 4 MiB cap; owner pages contain at
most the requested page count (owner maximum 100). Exactly one new Research
HTTP call per inbox read, zero per-ticket calls. The existing contributor has
at most four direct calls: consultation list/proposals/catalogue + Research
page. Other inbox contributors and existing display-name enrichment are not
included in that count. PATCH performs one original GET and one PATCH.

Reads run on user request only, at the existing three-trading-persona hosted
scale (catalogue maximum nine scheduled meetings/week, plus separately
human-convened meetings). Reload frequency is user-driven, not a new cadence.
Each owner gets at most 40% of the existing inbox contributor timeout (0.4 s
with its default 1 s), within the parent management deadline; consultation
rechecks remaining allowance before each of its calls. Transient unavailability
is displayed as source failure, never converted to human-only work.
BFF adds zero persistent records/cache entries/jobs, zero bytes/day storage
growth and no retention obligation. Request memory is released by normal
request completion/GC. R owns storage-manifest edits; this lane adds no writer.

## Residual findings and composition owners

1. **Open, consultation owner / MEETING-ACTION-ADOPTION-AND-REVIEW-20261123:**
   `list_meeting_proposals_view` currently enumerates all meeting rows and
   hydrates proposal statuses before filters; `list_meetings_view` also scans
   the archive before pagination. The BFF adds no new scan, but cannot certify
   whole-chain bounded owner reads until this is repaired at consultation.
   Preserve existing proposal semantics while adding filtered owner pages.
   A cross-task governed note was rejected by the ORCH_TASK_ID worker lease;
   the finding is retained here and in this task's canonical progress note for
   coordinator attachment to RC2 WP2.3 / RC6 meetings.
2. **Unproven composition / chain proof task:** Producer live status, safe
   capabilities, actual Research transition/evidence/concurrency checks and
   the fresh two-meeting loop require the other component deliveries and
   `MEETING-ACTION-TWO-MEETING-PROOF-20261123`. These BFF HTTP doubles are not
   evidence that those owners ran or that an action was completed on hosted.

Read-only `acceptance_probe.py` can check an existing hosted record using
BFF_BASE_URL, BFF_AUTH_TOKEN, BFF_TENANT_ID, MEETING_ID. It emits only counts,
byte sizes and result codes, performs zero writes, and exits 77 for missing
configuration or absent real actions. It supplements the chain-owned
executable probe; a read projection alone does not prove execution/acceptance.

## Privacy review correction

Codex rejected head `4dfc6564cf32051127d5b21e7ff10cd1a36834f7` on PR #7761:
the action projection removed a withheld scalar ID but retained its citation
mapping's excerpt, and a padded scalar ID bypassed the check. The correction
rejects the whole mapping when any ID from the existing `_extract_item_ref_ids`
helper is withheld and uses the rounds' existing whitespace normalization for
scalar refs. No extra reader, compatibility path or storage change is needed.

Authenticated regressions cover all six reference ID keys, exact and padded
IDs, a mapping containing both public and withheld IDs, nested recorded
dispositions, current/prior actions and their synthesis/fallback forms. Each
case runs as operator, owning trader and unrelated viewer; public citations
remain, trader citations follow viewer authorization, private payloads are
absent and owner history is unchanged. The test reproduced the leak before
the fix (exit 1), then all six parameter combinations passed (exit 0).
The full ten-file BFF regression batch passed with 175 tests, 100 warnings
in 88.11 s (terminal exit 0). The prescribed checkout Python environment was
then provisioned and all six privacy cases passed there too (1.49 s, exit 0).
Exact commands are recorded in `evidence.json`.

Codex subsequently rejected head `23d5628e9101c9b86ab238849b33299066ad902c`:
an explicit top-level null skipped filtering of the copied synthesis field.
Anchor `ebd592331` uses synthesis when the top-level field is absent or null,
then applies the existing visibility filter before returning either copy.
Explicit non-null values, including empty lists/maps, retain precedence.
All seven action fields use this path. No caller, stored history or owner
contract changes; the latest dev work-package registration is merged too.

Before the fix, the expanded authenticated regressions reproduced 30 failures
(48 passes, exit 1). After the fix, all 78 cases passed (14.31 s, exit 0):
current/prior null separately and together; all seven fields with absent,
null or empty top-level values; operator, owning trader and unrelated viewer;
whole withheld citations, private memory and foreign tenant subtrees. Public
records remain and original owner history is unchanged. The reviewer's original
six cases also passed (3.77 s). The current-base ten-file regression passed
247 tests, 100 warnings in 101.83 s (terminal exit 0). Exact commands are in
`evidence.json`.

Historical reviewer-family exception: earlier dispatch selected Codex, in the
same model family as owner Codex2, as recorded in the rejecting review. Current
canonical reassignment selects independent reviewer Antigravity. Re-handoff of
unchanged head `96f12e1ed` exited 1 because its Reviewer trailer still named
Codex. The additive review-metadata correction carries the current reviewer
without rewriting published history or changing product source. This packet
requests a fresh review; it claims no approval.

## Source-isolation review correction

Codex rejected head `21644e8b59fd4a0cc40219555d02ccee5bd7ddd7`: although
registration admitted Research, an earlier guard returned an empty inbox when
the read store and Consultation were absent. Anchor `7f0a70cf6` resolves the
existing contributor readers first and uses the store-missing response only
when no reader is available. This removes the obsolete duplicate admission
check; contributor registration remains the single decision point. Existing
store/Consultation callers and the no-source unavailable response remain.

The authenticated regression covers both an available empty read store and a
missing read store, with Consultation unconfigured and Research healthy. Each
case reloads twice, checks exactly one original linked ticket and one filtered
Research call per request, verifies caller bearer/tenant forwarding, and keeps
the Consultation unavailable surface and aggregate partial flag visible.
Before the fix: 1 failed, 1 passed (exit 1). After: 2 passed (exit 0).
The ten-file regression passed 249 tests, 100 warnings in 102.26 s (terminal
exit 0). Checkout interpreter/import verification, diff budget and whitespace
checks passed; commands and results are recorded in `evidence.json`.
No writer, cache, cadence or resource bound changed; residual findings and
chain-owned hosted proof above remain open.

## Typed-reference and partial-page review correction

Codex rejected head `99ca1187a642b80c8acf6b8e1614bcfd5039382b`: typed trader
references bypassed action visibility, and an available paginated Research
owner was reported unavailable when Consultation failed. Anchor `b58644f32`
reuses `_shape_subject_ref` for typed trader/agora_user mappings at every
action depth. It preserves operator/owning-trader access and redacts other
viewers using the ordinary subject-reference contract, without mutating
minutes. Other trader records retain their existing filtering behavior.
Readable degraded owner pages now keep the contributor available as degraded;
rows and continuation tokens are retained through the aggregate inbox surface.

Authenticated tests cover both typed kinds, all seven action fields, nested
subject/evidence/disposition refs, top-level/absent/null synthesis fallbacks,
operator, owning trader and unrelated viewer. Inbox tests cover paginated and
final pages with both available and missing read stores, absent Consultation,
two reloads, one filtered Research call per request and retained cursor/rows.
Before correction: 11 failed, 11 passed (exit 1). After: 22 passed in 4.17 s
(exit 0). The reviewer's original reproducer passed all seven cases in 3.96 s
(exit 0). Exact commands and the broader validation result are in evidence.json.
The ten-file regression passed 269 tests, 100 warnings in 104.02 s (terminal
exit 0). Checkout packaging/import, diff budget and whitespace checks passed.
Resource bounds and residual owners above are unchanged. This requests fresh
independent review of component validation; hosted acceptance remains unproven.

## Direct human assignments and Persona input requests

Operator reopened head `323ac0cf336a` because the contributor excluded directly
human-owned open/in_progress/returned tickets. Source anchor `b1e162bf0`
replaces the waiting-only query with one owner-filtered `human_actionable=true`
page on the original ticket endpoint. Research implements this union before
SQL pagination in `RESEARCH-MEETING-ACTION-TICKETS-20261123` (inspected owner
head `ad5dec939`); no BFF archive scan, second inbox or local work store exists.
The continuation href forwards the same filter/caller scope and opaque cursor.

Each original inbox row exposes `response_action=submit` for a directly owned
human assignment, its current `required_actor`, deliverable/acceptance criterion,
revision and reviewer. `resumes_with` identifies that reviewer after submission.
Returned work retains prior evidence and `review_decision`, and uses its return
reason. Persona escalation exposes `response_action=provide_input` and resumes
the same accountable Persona. Submitted/accepted direct work and runnable
Persona work are absent from this human queue. Existing REVIEW disposition,
refusal and proposal callers retain their separate supported purposes.

The existing authenticated original-ticket PATCH already preserves explicit
actions, actor, revision, idempotency and durable receipts. Its implementation
is retained; owner permissions and transitions stay authoritative. The UI must
use `response_action` and the original ticket link/current revision, present
the criterion on direct submission and wait for the receipt. Inbox acknowledgement
does not deliver work. The read-only probe now queries the same human union.

Authenticated BFF route tests share one HTTP owner double between the real
inbox and ticket routes: both response paths; direct open/in_progress/returned;
submit -> reviewer return -> corrected submit; wrong approver, read-only viewer,
tenant mismatch, absent evidence, duplicate writes, stale revision, durable
receipt/readback, same Persona resumption and disappearance on reload. Pagination
places 200 unrelated Persona rows before 105 direct human rows, verifies one
100-item filtered call and continuation without duplicates, excludes another
approver's work and preserves operator visibility. These are component tests,
not a claim that the unmerged Research owner or hosted stack was exercised.
Fresh real-owner integrated proof remains with the existing two-meeting task.

Resource measurements in resources.json cover 50 returned direct-human and
50 Persona-input rows with 2000-character fields. On-demand reads only: one
Research call/page, four direct contributor owner calls maximum, 100 Research
rows/page and 4 MiB HTTP reply cap; zero new recurring jobs, persistent writers,
cache entries or daily BFF storage growth. Request data is released on response;
Research owns persistence/retention and the chain storage manifest. Prior privacy
and partial-source fixes remain covered. Residual owners listed above are unchanged.

Validation on the direct-human source/tests: 124 focused tests passed in 23.95 s;
the ten-file regression passed 282 tests, 100 warnings in 120.84 s. Both bounded
foreground batches reached terminal exit 0. Diff budget and whitespace checks
passed. This is a fresh exact-head review request, with no approval/merge,
deployment or hosted acceptance claim.

## Human requirement and inline evidence note continuation

Source anchor `b3596a8eb` consumes the existing `description` included by R
only in its authorized direct-human `human_actionable` page. M preserves the
chair's bounded `human_requirement` there. Direct work now shows that actual
requirement in `reason`/`summary`, rather than repeating the deliverable, and
also retains it as `human_requirement`. Returned work shows its return reason
while preserving the original requirement, prior evidence, deliverable and
criterion. Absent/null/blank legacy requirements explicitly say they were not
recorded; reads never backfill them. Persona assistance retains input_request
reason and does not consume ticket description. No new owner query is added.
Read-only source checks covered R `b99655d779b0` and M `50c566653033`; these
are contract inspections of separate task worktrees, not integrated execution.

The existing authenticated PATCH forwards `evidence_note={title,body,source_refs?}`
without any new implementation or writer. Eight HTTP-contract cases cover
direct `submit` and Persona `provide_input`, optional source refs and optional
existing evidence refs. They verify the exact raw payload at the owner HTTP
boundary, signed-in actor, original revision/header idempotency, mismatch
rejection, read-only/tenant denial, duplicate/stale commands, full owner note
receipt/detail readback, and disappearance from the original inbox on reload.
Persona input retains existing submission refs. The boundary double emulates
owner note receipts; it does not prove durable note insertion or atomicity.
R's real-owner integration and the existing two-meeting/hosted proof owners
retain those obligations. Twelve authenticated reason/reload cases also retain
the legacy meeting_review item and one Research page call per reload.

The focused continuation batch passed 20 tests in 14.33 s, terminal exit 0.
The ten-file regression passed 302 tests, 100 warnings in 140.36 s, terminal
exit 0. Checkout import validation and whitespace checks also passed.
Resource bounds and residual owners above remain unchanged; the reproducible
synthetic measurement now includes a 2000-character direct-human requirement.

## Caller-specific response permission

Reopened head `17381897f` had no `can_respond`, so an eligible operator's
original human response form stayed hidden. Source anchor `31a48fbc0` passes
the inbox route's authenticated identity through the existing ManagementService
contributor and emits a boolean on every original `meeting_action` row.
Absent/unknown caller and view-only roles default false. The projection uses
the same canonical `require_operator_role` gate as original ticket PATCH,
then mirrors the inspected Research owner `b99655d779b0` actor/state rules:
operator/admin may act for another owner; direct human open/in_progress/returned
work permits its owner; waiting_for_input additionally requires the designated
human and the owner's input role. A reviewer role alone cannot provide input.
Submitted/accepted work stays out of the human queue. Research still validates
every write, including revision, idempotency, evidence and current permissions.
The flag describes caller permission at read time, not a future write receipt.

`response_action`, `human_requirement`, `reason`, `resumes_with`, owner links,
revision and historical evidence retain their existing canonical shape. No
second writer, general authentication scheme, new route or owner request is
introduced. Test anchor `7ee7e276a` includes signed operator/admin/approver/
reviewer/viewer GET-to-original-PATCH cases for both submit and provide_input,
visible-but-unwritable work, another owner's privileged response, unknown
caller/kind/state and removal after response. The HTTP owner double now applies
Research's owner/reviewer/required_actor read visibility and input-role check;
these remain component contracts rather than real-owner execution evidence.

The focused file passed 168 tests in 66.15 s, terminal exit 0. Synthetic
100-row projection is now 1,304,450 bytes (owner page unchanged at 772,715).
The added false boolean costs 2,200 bytes/page. Cadence, count/caps, zero daily
BFF storage growth, no added cache and request cleanup stay as in resources.json.
The read-only probe requires the boolean but accepts false for a view-only
caller; real cross-component/hosted acceptance remains with the existing owners.

Residual contract composition: read-only inspection of FE committed anchor
`92340c0f09677290a6c12971631d65e98ef59a44` finds `can_respond` consumed by
`src/lib/bff-v1/management.ts`, while its adapter still reads next_transition,
resumes_label/resumes and missing_input/requested_material. Its response card
requires both canRespond and a recognized nextTransition. The existing
`FE-MEETING-ACTION-FOLLOWTHROUGH-20261123` owner must finish consuming canonical
`response_action`, `resumes_with`, `human_requirement`/`reason` in ongoing work.
This is recorded in evidence.json residual_findings; no frontend file changed.

The final ten-file regression passed 326 tests, 100 warnings in 139.48 s,
terminal exit 0. Checkout import validation, diff budget and whitespace checks
passed. All required local batches have terminal outputs; CI, independent
review, merge and hosted acceptance are not claimed by these local results.

## Consultation detail contract correction

Resumed from `9e6bd23e1` on clean task branch; current dev `0c806ffed` is
already contained. Before editing, inspected the original BFF detail caller
and Consultation task source `83fb536a6` (`get_meeting_detail_view`, capability
receipt and `read_meeting_actions`). The producer uses
`participant_action_capabilities`, `prior_actions_source` (including `pages`),
`meeting_series.subject_ref`, and `synthesis.action_reviews`. The BFF previously
looked only for its public names and therefore lost these recorded values.

Anchor `631cd7d5a` maps those sources to `participant_capabilities`,
`action_read` / `action_page_info`, `series_subject_ref`, and
`prior_action_reviews`. Existing public fields still take precedence, including
explicit empty lists/maps; null retains the owner fallback. Source status,
reason, capability denial/allowed deliverables and cursor remain unchanged.
Frozen action state and live_owner_status stay separate. No extra owner call,
writer, cache, backfill, endpoint or status vocabulary is introduced.

Every aliased source uses the existing visibility filter, including its parent
container before nested extraction. The returned `synthesis.action_reviews`
copy is filtered too. Tests use real ConsultationStore/get_meeting_detail_view
with the exact producer keys, then the authenticated normal BFF route twice;
they cover allowed and restricted capabilities, ok/degraded/partial/denied/
unavailable receipts, pagination, frozen/live state and unchanged stored data.
Operator, owning trader and unrelated viewer cases cover private, foreign and
withheld records plus typed subject redaction. Legacy no-action behavior and
the original caller-specific `can_respond` tests remain in the regression.
The full ten-file batch passed 343 tests in 121.66 s (exit 0). Loading the
actual committed Consultation implementation from `83fb536a6` and rerunning
the detail cases passed 17 tests in 3.46 s (exit 0); this verifies the producer
view without substituting BFF public names. Exact commands are in evidence.json.

The read-only hosted probe now requires recorded capabilities, series,
reviews and source/page receipts when actions exist. Local invocation exited
77 (`probe_configuration_missing`); this is skipped, not accepted. Actual
owner execution and hosted proof remain with the existing chain proof task.

## Residual finding: canonical handoff infrastructure hold

Owner: development tooling coordinator / supervisor (RC8 task authority).
On this dispatch, the governed progress process remained in
`locks_lock_inode_wait` on central `.orchestrator/task-state.lock` and was
stopped with SIGTERM (terminal exit 143). The subsequent exact-head
`AI_NAME=Codex2 REVIEW_PR=7761 REVIEW_HEAD_SHA=2b405ff5386df753e636c548ea6dc30a59fd1330`
handoff through the pinned command runtime also waited for that lock and
reached the explicit 120-second limit (terminal exit 124, no output). Neither
is a successful state transition. The local tests and normal branch push
succeeded. The coordinator must resolve the central lock holder and admit
the current published PR head for Antigravity review. No canonical JSON,
lock file or command runtime was edited or replaced. This finding does not
change product evidence or claim a reviewer decision.
