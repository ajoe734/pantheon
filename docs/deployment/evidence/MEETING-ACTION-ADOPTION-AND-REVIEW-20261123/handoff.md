# Meeting action adoption and review

Owner Codex2; assigned reviewer Codex. Source review pending. No hosted or natural
execution acceptance has been performed. RC2 WP2.3, cross-reference RC6 meetings
and MEETING-HOSTED-ACCEPTANCE-20261115.

## Source and callers

Baseline inspected before edits: ba7c32e071a5d357067afc08addd78736aa25fd8 (origin/dev).
The recovery brief described an earlier draft, but this clean task branch,
its reflog and visible task commits contained no such source. No previous
validation/approval is inherited. Anchor commits preserve the rebuilt work.

Current path: main.py meeting routes -> convene_persona_meeting -> existing
ConsultationStore meeting/ConsultRequest -> workflow_executor ->
execute_persona_meeting -> provider.obtain -> adapter normal round/synthesis
schema -> validate_and_build_synthesis plus explicit adoption validation ->
existing owner endpoints -> memos/learn-feedback -> existing detail route.
The Registry/member-facts case composer, citation/privacy checks, authorized
round anonymization and formal proposal sponsor/paper gates remain in that path.

Removed obsolete behavior: a missing explicit chair proposal no longer adopts
round-2 proposal suggestions automatically. Suggestions remain advisory until
explicit chair output. No caller needs that implicit adoption fallback: the
normal synthesis schema already owns proposals. Research demand routing remains
for pool discovery, all eight formal proposal kinds retain their owner-specific
gates, and historical zero-action meetings are still read without backfill.
There is no replacement task store, scheduler, inbox, detail page or migration.

## Owner contracts and recovery

ResearchWriteOwner owns identity, mutable progress, evidence and acceptance.
POST omits ticket_id; a real returned ticket ID/tenant/origin/state/revision is
required before freezing a link. Empty, malformed, foreign or mismatched 2xx
replies remain retryable. The original request/source_action_id is retained in
the existing dispatch ledger; successful replay does not POST again. No second
hash. An unresolved dispatch leaves the original workflow unclosed and uses its
existing retry/lease budget. Synthesis is frozen before close side effects, so a
lost owner reply or blocked memory writeback never asks the model to adopt a
new action. Closed execution retries also reuse the receipt.

Persona's GET /api/personas/{id}/action-capability?run_purpose=daily_cycle is the
capability authority. Only a safe whitelist enters the model; role labels and
inline roster snapshots grant nothing. The consultation session remains deny-all.
A new Persona research assignment needs owner status=allowed,
can_receive_persona_action=true and research_note in allowed_deliverables.

MT-1 joins the verified owning Persona pool across weekly meeting IDs. Other
meetings join only an explicit stable subject; absent/ephemeral subjects without
an established identity are unavailable, never a global type-based series.
Three independent bounded owner pages retain outstanding, submitted and newly
accepted work, including explicit continuation links/tokens. No full meeting
archive scan is introduced. Actual note contents (or explicit unavailable/changed/
metadata-only reasons) appear in participant and chair input with criterion,
evidence and disposition. Each lane receives an independent whole-ticket evidence allocation. Acceptance
requires an exact criterion/evidence match and a reason, and PATCHes the same
owner revision with review_source=current meeting. Owner CAS/evidence validation
remains authoritative. A failed owner ticket page retains its scan boundary and input cursor. Evidence
failures are explicit, forbid acceptance, and retain the old watermark for the
next traversal while allowing current ticket-page progress. The existing schedule automatically continues each bounded lane. A
finished scan advances the inclusive watermark only to its first page start,
so concurrent insertions and disposition changes behind the cursor are read
next scan; legacy bare cursors restart safely. Bare metadata cursor authority
was removed: only persisted schedule cursors continue a scan, including when a
failed first page left an empty cursor map. No supported caller needs an
unbound cursor to override the authoritative scan boundary. Minutes keep frozen snapshots; detail adds separate
live_owner_status and never writes on a read.

## Review rejection remediation

The d2c889826 review rejected cursor/watermark loss and whitespace receipt
mismatch. Adoption now strips agreed text fields/source refs before capability,
identity checks and freezing. Lost-receipt retries have identical request bodies.
Regressions load actual ResearchWriteOwner create/list from cddd6188fc784ced558d6a800e95584aef17636a;
only persistence is isolated (list SQL executes in SQLite with dialect translation).
Set MEETING_ACTION_RESEARCH_OWNER_SOURCE to a git-exported write_owner.py to rerun
before that owner package merges; afterward tests use its normal module import.
Source SHA256: 056e0aa3a472e75de633e6a0553ae5578ab80a51911b54b5c64e11261a8c6290.
Tests cover new accepted records plus updated dispositions below a live cursor,
five schedule participants, bounded pages, duplicate canonical IDs and due/review.
A broad rerun also exposed cross-second partial-close memo timestamps changing;
the existing published dissent timestamp now anchors both immutable memos, while
closed_at records actual completion. The two-meeting test forces a five-second
retry. No extra writer or recurring job was added.

## Evidence starvation rejection (fa240d554)

The old eight-read global budget plus all-lane evidence gate could repeat the
first page forever. The reader now asks the owner for one ticket per lane,
resolving all its at-most-eight references (at most 24 reads across three
lanes). The original owner cursor remains opaque and is never manufactured.
This deliberately trades page throughput for a whole-ticket evidence bound:
N stable tickets in a lane take N same-series meetings per traversal. It adds
no cache, scheduler, evidence copy between revisions, or second work store.

Successful ticket pages advance independently of unavailable evidence. A sticky
evidence-failure flag prevents a later healthy page from advancing the watermark
past failed obligations; the next traversal retries from the prior watermark.
Completed lanes do not restart while other lanes are still traversing. The
original scan-start watermark and concurrent earlier-key insertion/disposition
guarantees remain. No legacy caller needs the exhausted-evidence branch; its
replacement completely reads each selected ticket's bounded evidence set.

Regression matrix uses both the scripted boundary and frozen real Research
create/list SQL: 9 returned, 10 submitted and 11 accepted tickets, each with
2 or 8 evidence refs; 22 distinct meeting identities and actual ConsultationStore
schedule writes per case. Clean, transient-503 and persistent-503 evidence
scenarios prove every ticket appears in two successive traversals, actual
contents reach the context, unread evidence cannot be accepted, independent
lanes finish, failed evidence retries, and requests/bytes stay bounded.
Existing five-participant concurrent-insertion tests now traverse nine pages.

## Validation and resource budget

See evidence.json, validation.txt, fair-validation.txt, measurements.txt and
storage.json. The normal adapter schema and engine cover two distinct meetings,
pending plus submitted tickets, accept/return with reason, zero semantic hits,
actual result contents in each turn, malformed-reply retry, partial close retry,
no repeated model call, cross-series/tenant isolation, and historical minutes.
The read-only probe.py checks recorded meeting component receipts; the final
MEETING-ACTION-TWO-MEETING-PROOF task owns real-owner integration, Persona actual
execution and browser/natural hosted proof. Do not call component stubs hosted proof.

R owns storage-manifest edits. storage.json supplies measured bytes, cadence,
counts, growth/day, caps and cleanup for the existing writers. Existing archive
retention debt is explicit. New model requests cap at 2 MiB, case files at
256 KiB; reference/page bounds never silently evict unfinished obligations.

## Residual findings and dispositions for coordinator

- RC6 consultation/BFF projection owner: legacy list_meetings_view and
  list_meeting_proposals_view scan the archive. Retain that existing-domain
  obligation; the new action path uses filtered pages and direct ticket links.
- RC3 WP3.1/3.3 storage owner: no verified whole-history cap/cleanup for meeting
  lifecycle and dispatch ledger; the ledger still rewrites its full history.
  New per-record caps do not imply whole-archive boundedness. Retain open work.
- Existing Research artifact owner / RC2 WP2.3: saved artifact detail returns
  metadata only and internally scans the artifact/run archive. It is not actual
  deliverable content. This path records metadata_only_result_not_read and will
  not accept it; genuine meeting-bound research-note contents are supported.
  Do not create a duplicate reader or claim metadata-only content was reviewed.
- Delivery/authentication owner and final chain integrator: consultation's
  compose environment currently wires no dedicated Research assignment
  credential. Configure an issued consultation-svc/service token via
  CONSULTATION_RESEARCH_SERVICE_TOKEN[_FILE], or the existing configured signing
  secret contract in an authorized environment. This implementation has no
  baked-in secret or operator impersonation; missing credentials are retryable
  owner-unavailable. Hosted activation is pending that deployment wiring and the
  Research/Persona owner changes; no infrastructure files changed here.
- Coordinator/reviewer: canonical assignment is Codex2 -> Codex (same family).
  Apply the repository's independent-family capacity rule, recording an actual
  exception if needed; this owner has not manufactured capacity or approval.
- Original MEETING-HOSTED-ACCEPTANCE-20261115 / final two-meeting proof owner:
  natural second meeting, real authorized human input, actual Persona execution,
  exact hosted pair and browser evidence remain pending.
