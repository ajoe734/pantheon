# BFF source review handoff

Owner: Codex2. Reviewer: Codex (current canonical reassignment). Delivery target: `dev`.
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
  authorized `meeting_linked=true&action_state=waiting_for_input&page_size=100`
  Research owner page. Each original ticket produces one `meeting_action`
  entry, linked to the same meeting/action and original ticket facade. It
  carries input reason, required material, original owner, actor required now,
  resume owner, revision, due/review, overdue and original dates. A REVIEW
  disposition remains its own item and cannot deliver or accept ticket work.
* Existing `GET /api/v1/research/tickets?meeting_linked=true` forwards owner,
  meeting_type, stable subject_ref, action_state, date filters, page size and
  opaque page token. Owner `data` and `page_info` are preserved. The inbox
  surface exposes the Research continuation and facade link; a nonempty owner
  cursor marks the aggregate partial, so its local total is not an archive
  census. Continue via the ticket facade; no archive walk or ticket fanout.
* Existing ticket PATCH forwards the authenticated caller and verified tenant
  to Research. `action=provide_input`, original `expected_revision`,
  `idempotency_key`, `evidence_refs` (owner-issued kind/artifact_id) and any
  allowed review command are validated by the owner. An Idempotency-Key header
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
100 distinct waiting tickets with 2000-character deliverable, criterion and
input reason: owner JSON 683570 bytes, inbox rows 1192200 bytes.
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

Reviewer-family exception: the current explicit dispatch/canonical assignment
selects Codex, in the same model family as owner Codex2, as already recorded in
the rejecting review. This packet requests a fresh review; it claims no approval.

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
