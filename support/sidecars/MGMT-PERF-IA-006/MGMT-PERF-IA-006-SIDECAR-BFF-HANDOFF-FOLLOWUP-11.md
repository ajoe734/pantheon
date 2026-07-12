# MGMT-PERF-IA-006 BFF Handoff Follow-up 11

| Field | Value |
|---|---|
| Parent task | `MGMT-PERF-IA-006` |
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-11` |
| Owner / reviewer | `Codex` / `Antigravity` |
| Helper kind | `bff_handoff_packet` |
| Scope | support-only no-delta handoff and parent wake-up conditions |
| Mutates canonical or runtime | `false` |

This packet records the durable state after Follow-up 10. It does not repeat
the route inventory, define a query field, edit Pantheon BFF or
`execute-plans`, approve the parent, or authorize a mutation.

## 1. No-Delta Verdict

There is no new BFF or frontend contract delta for the parent to absorb:

- `MGMT-PERF-IA-003` remains `blocked`, waiting for human merge of green
  `execute-plans` PR #261 and subsequent hosted evidence;
- `MGMT-PERF-IA-004` remains archived `done`; its corrected Rankings Center
  delivery is the only completed Wave 1 dependency;
- `MGMT-PERF-IA-005` remains `blocked`, waiting for human merge of green
  `execute-plans` PR #260 and subsequent hosted evidence; and
- parent `MGMT-PERF-IA-006` remains `todo` and depends on all three Wave 1
  deliveries.

Follow-up 9 remains the substantive dependency gate, query-gap decision
table, operator journey, and frontend evidence bundle. Follow-up 10 remains
the delta checkpoint and redispatch gate. An open frontend PR is not durable
destination-contract truth, so this packet does not infer routes, tabs,
adapter behavior, accepted query keys, or deployed behavior from those PRs.

## 2. Parent Wake-up Conditions

The parent should resume contextual implementation only after it can record
the actual merged and deployed revision for each destination it consumes.

| Condition | Evidence needed before absorption | Owner action |
|---|---|---|
| Performance Center becomes durable | merged PR/SHA, deployed ancestry, accepted identity/period keys, and returned source/snapshot behavior | Reconcile every contextual link against the merged destination; do not copy provisional branch behavior. |
| Governance Decisions becomes durable | merged PR/SHA, deployed ancestry, Human Inbox destination/return behavior, and decision-versus-apply receipt semantics | Reconcile governed journeys and safe return behavior against the merged destination. |
| Parent finds a stable-ID mismatch | exact source response, destination contract, missing stable link, blocked acceptance criterion, and negative case | Mark `honest unavailable` when acceptance permits; otherwise create a separately owned Pantheon BFF task. |
| No mismatch is found | one evidence row per Cockpit, Fleet, entity detail, Human Inbox, and Agora origin | Record `absorbed` with only endpoint-accepted identity/context keys and returned fulfillment evidence. |

Display name, label, rank, actor, timestamp, or text matching is not a stable
identity bridge. Requested URL context is not proof that a destination
fulfilled the request.

## 3. Operator Journey Carry-forward

The parent evidence must prove the existing journey requirements on merged,
hosted revisions:

1. Enter from Cockpit, Persona Fleet, an entity detail, Human Inbox, or Agora.
2. Preserve only destination-supported identity, runtime/stage, period, and
   snapshot context through refresh, copied URL, and browser history.
3. Show returned source/snapshot state independently from requested URL state.
4. Keep compact entity summaries, formal attribution, formal rankings,
   governance decisions, apply receipts, and Agora execution diagnostics
   visibly distinct.
5. Render honest empty, unavailable, degraded, stale/fallback, unmatched, and
   incompatible-identity states without fixtures or fabricated zeroes.
6. Complete or cancel Human Inbox review and return only through an
   allow-listed canonical origin, without guessed identity or arbitrary URL.

Freeze, promotion, rebalance, allocation, access, broker, and runtime changes
remain governed writes. Navigation success is not an apply receipt. Agora
remains the execution-diagnostics surface in Trading Room rather than a
duplicate management-attribution surface.

## 4. Next Useful BFF Handoff

Do not dispatch another substantively identical `bff_handoff_packet` while
the durable facts above remain unchanged. A new support packet becomes useful
only when a dependency merges/deploys, the parent demonstrates a concrete
source/destination mismatch, a reviewed contract behavior changes, or the
reviewer requests a specific correction. This is task-scoped handoff advice,
not a change to supervisor routing policy.

When a concrete BFF gap exists, its separate task must name the source
response, destination contract, missing stable link, blocked acceptance,
authorization boundary, snapshot/pagination semantics, negative tests, owner,
and reviewer. This sidecar does not establish that such a task is currently
needed.

## 5. Review And Composition Handoff

Reviewer `Antigravity` should confirm the dependency posture, absence of an
invented route or field, fail-closed gap disposition, and isolation to this
support artifact. After approval, `Antigravity` as parent owner decides what
to absorb into `MGMT-PERF-IA-006`; parent reviewer `Claude` reviews the
eventual composed `execute-plans` delivery. Sidecar approval is not parent
approval and does not close either blocked dependency.

## 6. Verification

Re-read the task-scoped brief, base handoff, Follow-ups 9 and 10, and queried
durable task records with `AI_NAME=Codex ./scripts/ai-status.sh show` for
`003`, `004`, `005`, `006`, and this sidecar. Confirmed that no newer contract
delta is durable. No canonical document, Pantheon BFF runtime/schema,
registry, governance implementation, supervisor policy, or frontend file was
changed. `current-work.md` and the complete `ai-activity-log.jsonl` were not
scanned.
