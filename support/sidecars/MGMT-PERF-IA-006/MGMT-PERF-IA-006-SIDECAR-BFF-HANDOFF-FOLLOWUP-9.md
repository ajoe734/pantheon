# MGMT-PERF-IA-006 BFF Handoff Follow-up 9

| Field | Value |
|---|---|
| Parent task | `MGMT-PERF-IA-006` |
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-9` |
| Owner / reviewer | `Codex` / `Antigravity` |
| Helper kind | `bff_handoff_packet` |
| Scope | support-only dependency-aware handoff gate |
| Mutates canonical or runtime | `false` |

This packet gives the parent owner a current, bounded gate for absorbing the
existing BFF/frontend handoff. It does not define a route or field, edit
Pantheon BFF or `execute-plans`, approve the parent, or authorize a mutation.

## 1. Current Dependency Gate

At preparation time, `MGMT-PERF-IA-004` is archived `done`. Its Rankings
Center delivery and seeded-fallback correction are durable. In contrast,
`MGMT-PERF-IA-003` and `MGMT-PERF-IA-005` remain blocked pending human merge
of their green `execute-plans` PRs and subsequent hosted evidence. Therefore:

- the parent may prepare contextual adapters against reviewed dependency
  shapes, but must compose against the actual merged SHAs;
- an open or locally reviewed dependency PR is not destination contract truth;
- the parent cannot claim completion until all three dependency revisions and
  the contextual integration revision are deployed and evidenced; and
- this sidecar does not replace either blocked dependency's owner closeout.

| Dependency | Current durable posture | Parent intake requirement |
|---|---|---|
| `MGMT-PERF-IA-003` Performance Center | Blocked; frontend PR awaits human merge and hosted proof | Record merged PR/SHA, deployed ancestry, canonical tabs, and accepted context keys before treating links as fulfilled. |
| `MGMT-PERF-IA-004` Rankings Center | `done`; merged delivery recorded | Compose with the corrected live-only Rankings Center revision and preserve rolling versus quarterly scope. |
| `MGMT-PERF-IA-005` Governance Decisions | Blocked; frontend PR awaits human merge and hosted proof | Record merged PR/SHA, governed destination/return behavior, and receipt semantics before wiring review journeys. |

## 2. BFF Query-Gap Decision Table

Existing packets establish no universal backend context token. For each
implemented entry point, the parent must record the actual source identifier,
destination read, accepted query fields, and returned snapshot/source state.

| Check | Absorb with existing reads | Honest unavailable | Separate BFF task |
|---|---|---|---|
| Identity | Source returns a stable ID accepted by the merged destination. | ID is absent, ambiguous, or unsupported and parent acceptance permits a reasoned unavailable state. | A required journey lacks a stable accepted ID and unavailable would violate explicit acceptance. |
| Period/snapshot | Destination accepts compatible period/as-of context and returns fulfillment evidence. | Requested context cannot be fulfilled or returned evidence is stale, degraded, fallback, or incompatible. | A durable snapshot/evidence link is required across surfaces but no existing payload carries it. |
| Human Inbox return | An allow-listed canonical origin and compatible IDs reconstruct the journey. | Return safely to a canonical center with a visible lost-context reason. | Required decision identity or safe return context is absent from the source/destination contract. |
| Multi-read composition | Each section retains independent health, snapshot, loading, empty, and error state. | The affected section alone is unavailable/degraded. | Only if explicit acceptance requires an atomic linkage that existing reads cannot prove. |

A separate BFF task must name the exact source response, destination contract,
missing stable link, blocked acceptance criterion, authorization boundary,
snapshot/pagination behavior, negative tests, owner, and reviewer. Do not close
a gap with display-name, label, rank, actor, timestamp, or text matching, and
do not invent a route, query key, field, or durable return token here.

## 3. Operator Journey Proof

The parent evidence should prove these transitions against merged and hosted
revisions:

1. Enter from Cockpit, Persona Fleet, an entity detail, Human Inbox, or Agora.
2. Preserve only destination-supported identity, runtime/stage, period, and
   snapshot context in the canonical URL.
3. Render destination-returned source and snapshot evidence; requested URL
   values alone do not prove fulfillment.
4. Keep compact entity summaries, formal attribution, formal rankings,
   governance decisions, apply receipts, and Agora execution diagnostics
   visibly distinct.
5. Exercise healthy empty, unavailable, degraded, stale/fallback, unmatched,
   and incompatible identity states without fixtures or fabricated zeroes.
6. Prove refresh, copied URL, back/forward, and Human Inbox completion and
   cancellation return without guessing identity or accepting arbitrary URLs.
7. Keep freeze, promote, rebalance, allocation, access, broker, and runtime
   changes in governed Human Review; completion requires the applicable apply
   receipt rather than navigation success.

## 4. Frontend Handoff Bundle

Before the parent enters review, attach:

- the exact merged and deployed SHAs absorbed from `003`, `004`, and `005`;
- one row per origin naming returned source ID, canonical destination,
  endpoint-accepted query fields, preserved compatible context, and
  disposition (`absorbed`, `honest unavailable`, or `separate BFF task`);
- focused typed-context parse/serialize, endpoint allow-list, pagination
  reset, redirect-loop, browser-history, and safe-return tests;
- authenticated strict-live captures for every consumed BFF read, including
  returned source health and snapshot/period evidence;
- hosted desktop/mobile proof that identity, analytical scope, source state,
  unavailable reason, primary action, and return behavior remain legible; and
- separately owned BFF gap tasks, or an explicit evidence-backed statement
  that existing stable identifiers were sufficient.

Agora remains in Trading Room with execution-diagnostics scope. Entity pages
remain compact summaries. Formal attribution and ranking remain in their
canonical centers. The frontend must not add a new analysis surface merely to
avoid a contextual-link gap.

## 5. Reviewer And Composition Handoff

Reviewer `Antigravity` should verify that the dependency posture above still
matches durable task records, no endpoint or field is invented, unavailable
states remain fail-closed, and only this support artifact is committed.

After sidecar approval, parent owner `Antigravity` decides what to absorb and
routes any implementation or backend gap to a separately owned task. Parent
reviewer `Claude` evaluates the composed `execute-plans` delivery. Approval of
this packet is neither parent approval nor evidence that blocked dependencies
have merged or deployed.

## 6. Verification

Re-read the task-scoped brief, parent execution packet, base handoff, and
follow-ups 2–8. Queried durable task state with `AI_NAME=Codex`: `004` is
archived `done`; `003` and `005` remain blocked pending human frontend merge
and hosted evidence. Confirmed this packet preserves the existing query-gap,
operator-journey, analytical-scope, honest-unavailable, and separate-owner
boundaries without introducing a new endpoint or field claim. No canonical
document, Pantheon BFF runtime/schema, registry, governance implementation, or
frontend file was changed. `current-work.md` and the complete
`ai-activity-log.jsonl` were not scanned.

## 7. Owner Closeout

Reviewer approval is recorded in
`support/reviews/MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-9-review-antigravity.md`.
Owner finalization confirmed that the approved packet remains support-only and
that no canonical, runtime, registry, governance, or frontend source was
changed. PR #3357 merged the approved packet and review record into `dev` at
`fad35d92d43cefba3799b43cb9727b9183a4279c`; repository branch checks passed.
