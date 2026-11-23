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
evidence and disposition. Submitted work gets evidence-read priority. Acceptance
requires an exact criterion/evidence match and a reason, and PATCHes the same
owner revision with review_source=current meeting. Owner CAS/evidence validation
remains authoritative. A failed owner/evidence/page read does not advance the
existing schedule watermark. Minutes keep frozen snapshots; detail adds separate
live_owner_status and never writes on a read.

## Validation and resource budget

See evidence.json, validation.txt, focused-validation.txt, measurements.txt and
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
