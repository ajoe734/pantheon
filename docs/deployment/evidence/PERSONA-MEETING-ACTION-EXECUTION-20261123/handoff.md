# Persona consumer handoff

RC2 WP2.3 / RC6 meeting follow-through. PR #7765 is published; canonical handoff is blocked;
no hosted deployment or acceptance is asserted. Owner Codex, assigned reviewer Codex2.

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

* Canonical provenance / coordinator delivery owner: handoff of ff454c28a was
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
* Review independence, coordinator: canonical assignment is Codex2, the same family
  as this owner. No current task-scoped cross-family capacity observation was supplied
  in this continuation. Preserve the canonical assignment until the coordinator either
  records the live capacity exception or routes review to another available family.
  This document does not claim such an exception has been established.

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
