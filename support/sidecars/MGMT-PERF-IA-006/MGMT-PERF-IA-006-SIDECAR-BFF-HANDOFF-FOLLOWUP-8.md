# MGMT-PERF-IA-006 BFF Handoff Follow-up 8

| Field | Value |
|---|---|
| Parent task | `MGMT-PERF-IA-006` |
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-8` |
| Owner / reviewer | `Codex` / `Antigravity` |
| Helper kind | `bff_handoff_packet` |
| Scope | support-only implementation intake |
| Mutates canonical or runtime | `false` |

This packet gives the parent owner a bounded intake record for composing the
existing BFF/frontend handoff with the actual `execute-plans` implementation.
It does not define a route or field, edit Pantheon BFF or frontend source,
approve the parent, or authorize a mutation.

## 1. Dependency Intake

Before implementing or reviewing contextual links, record the exact merged
revisions absorbed from `MGMT-PERF-IA-003`, `004`, and `005`. Do not infer a
route, tab, query key, or response shape from their task descriptions.

| Dependency | Merged PR / SHA | Absorbed route or adapter surface | Contract or evidence reference | Residual incompatibility |
|---|---|---|---|---|
| `MGMT-PERF-IA-003` Performance Center | Parent records | Parent records | Parent records | Parent records |
| `MGMT-PERF-IA-004` Rankings Center | Parent records | Parent records | Parent records | Parent records |
| `MGMT-PERF-IA-005` Governance Decisions | Parent records | Parent records | Parent records | Parent records |

An unfinished dependency may be inspected, but must not be treated as merged
contract truth. A residual incompatibility is either handled by an honest
unavailable state or routed as a separately owned gap; it is not closed with a
browser-side heuristic join.

## 2. Context-Link Intake Matrix

Complete one row per implemented entry point. The source response must provide
the stable identifier consumed by the destination. Requested URL context is
navigation intent; returned identifiers, source health, and snapshot/period
metadata are fulfillment evidence.

| Origin / action | Returned source ID | Canonical destination / accepted ID | Compatible context preserved | Return behavior | Disposition / evidence |
|---|---|---|---|---|---|
| Cockpit card or alert | Parent records | Performance or Rankings center | Entity, stage, period as supported | Back/history restores origin | Parent records |
| Persona Fleet or Persona Detail | Parent records | Performance or Rankings center | Persona, runtime, period as supported | Summary remains distinct from formal analysis | Parent records |
| Strategy Detail | Parent records | Performance Center or Agora | Strategy and period as supported | Scope change is visibly labelled | Parent records |
| Human Inbox decision | Parent records | Allow-listed originating center | Compatible entity, snapshot, period | Completion and cancellation both restore safely | Parent records |
| Capital Pool / Rebalance / Ranking Policy detail | Parent records | Existing live destination, if any | Only contract-supported context | Honest empty/unavailable when absent | Parent records |
| Agora Strategy Performance | Parent records | Management Performance link | Strategy and period as supported | Return restores Trading Room context | Parent records |

Each row receives exactly one disposition:

- **absorbed** — stable identity and compatible context round-trip through
  refresh, copied URL, browser history, and applicable Inbox return;
- **honest unavailable** — authoritative identity or data is absent and the UI
  exposes the reason without fixtures, fallback authority, fabricated zeroes,
  or inferred joins; or
- **separate BFF gap** — an explicit parent acceptance criterion cannot be met
  because the source lacks a stable identifier accepted by the destination.

## 3. BFF Gap Delta

Existing handoff packets do not prove a universal backend context token across
Cockpit, Fleet, entity details, formal centers, Human Inbox, and return. The
parent must not invent one. If a row requires a separate BFF task, hand off this
minimum delta:

1. exact source route and actual returned identifier or missing link;
2. exact destination read and identifier/query shape it currently accepts;
3. blocked origin-to-destination journey and the acceptance criterion that
   makes honest unavailable insufficient;
4. authorization and tenant boundary, snapshot/pagination behavior, and
   negative contract tests;
5. separate Pantheon BFF owner and reviewer.

Do not choose a speculative endpoint, parameter, response field, or durable
return token in this frontend sidecar. Display name, rank, label, actor,
timestamp, and matching text are never identity keys.

## 4. Operator Proof Sequence

The parent evidence should demonstrate a continuous, reproducible sequence:

1. enter from Cockpit, Fleet, an entity detail, Human Inbox, or Agora;
2. retain only supported identity and period context in the canonical URL;
3. load the destination through its existing BFF adapter and expose the
   returned source/snapshot state;
4. distinguish compact summary, formal attribution, formal ranking,
   governance context, and Agora execution diagnostics;
5. exercise healthy empty, unavailable, degraded, stale/fallback, and
   incompatible identity behavior without fixture authority;
6. refresh, copy the URL, use back/forward, and complete or cancel an Inbox
   journey without losing or guessing context; and
7. confirm that any mutation remains in governed Human Review and is not
   represented as complete without an apply receipt.

## 5. Parent Handoff Bundle

Before parent review, attach:

- completed dependency and context-link matrices;
- merged `execute-plans` PR and merge SHA plus hosted deployment ancestry;
- focused context serializer/parser, endpoint query allow-list, pagination
  reset, redirect-loop, history, and safe-return test results;
- authenticated strict-live captures for every consumed BFF read, including
  section-local health and snapshot evidence;
- hosted desktop and mobile proof that identity, period, analytical scope,
  source state, primary action, and honest unavailable behavior remain legible;
- either every separately owned BFF gap task or an explicit statement that
  existing stable identifiers were sufficient.

Parent owner `Antigravity` decides whether to absorb this intake record and
owns implementation/gap routing. Parent reviewer `Claude` evaluates the
composed delivery. Sidecar reviewer `Antigravity` verifies only that this
artifact is accurate, useful, and support-only.

## 6. Verification

Re-read the task-scoped brief, parent execution packet, base handoff, and
follow-ups 2–7. This intake preserves their query-gap, operator-journey,
analytical-scope, honest-unavailable, dependency, and separate-owner
boundaries without introducing a new endpoint or field claim. No canonical
document, Pantheon BFF runtime/schema, registry, governance implementation, or
frontend file was changed. `current-work.md` and the complete
`ai-activity-log.jsonl` were not scanned.
