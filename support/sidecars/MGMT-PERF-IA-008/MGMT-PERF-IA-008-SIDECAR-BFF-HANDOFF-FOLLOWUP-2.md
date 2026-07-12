# MGMT-PERF-IA-008 BFF Handoff Follow-up 2

| Field | Value |
|---|---|
| Sidecar task | `MGMT-PERF-IA-008-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Parent task | `MGMT-PERF-IA-008` — Hosted acceptance and closeout |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Antigravity` / `Claude` |
| Sidecar owner / reviewer | `Codex` / `Antigravity` |
| Date | `2026-07-12` |
| Mutates canonical truth | `false` |

This support-only follow-up turns the existing umbrella handoff into a bounded
capture contract for the parent acceptance run. It does not add BFF routes or
fields, change frontend source, define canonical semantics, or constitute
hosted evidence.

## 1. Capture Record Per Hop

For every Fleet, Performance, Rankings, Governance, and Human Review request,
the parent evidence index should record:

- `journey_step`, request method/path, redacted query/body, response status,
  capture time, frontend URL, viewport, and Pantheon/`execute-plans` deployed
  revisions;
- requested context and response-fulfilled context separately: persona,
  runtime, strategy, pool/sleeve, broker, deployment stage, period/quarter,
  snapshot/as-of, recommendation, review, operation, and receipt identifiers;
- response-authored source/coverage/freshness/confidence state, missing-binding
  diagnostics, pagination metadata, and links exactly as returned;
- the screenshot or assertion reference that renders the response, plus any
  console or network failure.

Omit fields that a response does not supply; do not synthesize them. Redact
authorization headers, cookies, credentials, and personal data.

## 2. BFF Query Gap Triage

| Observed gap | Parent disposition |
|---|---|
| Requested filter is absent from fulfilled response context | Mark context preservation unproven at that hop. Do not use the URL alone as evidence. |
| Ranking has no stable evidence/snapshot reference | Do not connect it to a recommendation by rank, label, persona name, or time; open a bounded BFF contract follow-up. |
| Recommendation has no stable Human Review id/link | Stop the governed journey at recommendation submission and record a traceability blocker. |
| Review action has no operation/receipt id or retrievable link | Report safely non-applied. A success toast or HTTP 2xx is not apply proof. |
| Page shell is healthy but a composed query is partial, stale, degraded, unavailable, or failed | Preserve the section-level state; do not promote the page to healthy/formal. |
| Pagination/count/snapshot metadata is absent | Limit the claim to visible returned rows; do not claim a complete cohort or client-side official ranking. |
| Legacy redirect drops an allow-listed context value | Record the first lost value, initial/final URL, and redirect count as an IA blocker. |

No closeout-only aggregate endpoint is requested. Any implementation follow-up
must be owned by the relevant parent/backend lane and reviewed against existing
contracts before route or schema changes are made.

## 3. Frontend Handoff Rules

The `execute-plans` acceptance runner should:

1. use strict live BFF mode and record the actual frontend/BFF origins;
2. preserve typed query context between canonical links, while displaying
   response-fulfilled context when it differs from the request;
3. reset pagination on tab, filter, period, quarter, or snapshot changes;
4. format only finite metrics and keep healthy-empty, partial, fallback,
   stale, degraded, unavailable, unauthorized, and transport failure distinct;
5. preserve backend-authored rank and unknown enum values without renumbering
   or coercing them to a healthy default;
6. navigate from ranking evidence to recommendation and Human Review only by
   stable BFF-provided identifiers/links;
7. retain originating context on return navigation and repeat the essential
   journey at a mobile viewport.

Frontend-only joins by labels, display names, ranks, or timestamps are not an
acceptable substitute for a missing BFF identity link.

## 4. Parent Run Sheet

- Record child tasks `MGMT-PERF-IA-001` through `007`, their PRs, merge SHAs,
  reviewer verdicts, and deployed ancestry or explicit supersession evidence.
- Capture one formal performance path and one honest non-formal path.
- Capture Rolling and Quarterly ranking context without treating client sort as
  official rank.
- Prove `ranking evidence -> recommendation -> Human Review -> receipt`, or
  stop at the first missing stable link with a precise safely non-applied
  blocker.
- Crawl canonical and documented legacy routes on desktop and mobile, storing
  initial/final URL, redirect count, retained context, status, and failures.
- Confirm Rankings is the only full ranking-table owner and Governance consumes
  references rather than recreating an authoritative ranking table.
- Store the capture index and artifacts under the parent-declared archive; this
  packet itself is not acceptance evidence.

## 5. Reviewer And Parent Handoff

Reviewer `Antigravity` should confirm that this follow-up is support-only,
requires response-authored identities and source states, fails closed at
missing recommendation/review/receipt links, and does not invent a route or
schema. After approval, parent owner `Antigravity` may absorb the run sheet;
parent reviewer `Claude` remains responsible for the composed closeout verdict.

Recommended status transition:

```bash
AI_NAME=Antigravity ./scripts/ai-status.sh approve \
  MGMT-PERF-IA-008-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 \
  "Support-only BFF capture and frontend handoff follow-up approved for parent absorption."
```

## 6. Preparation Evidence

- Prepared on
  `task/MGMT-PERF-IA-008-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` from `dev`.
- Read the task-scoped brief, collaboration guide, worker anchor protocol,
  closeout protocol, parent execution packet, and existing umbrella sidecar.
- Inspected relevant BFF route/action references without changing runtime or
  frontend files.
- Did not scan `current-work.md` or the full `ai-activity-log.jsonl`.
