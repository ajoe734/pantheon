# MGMT-PERF-IA-006 BFF Handoff Follow-up 19

| Field | Value |
|---|---|
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-19` |
| Parent task | `MGMT-PERF-IA-006` — Contextual integration |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Antigravity` / `Claude` |
| Sidecar owner / reviewer | `Codex` / `Antigravity` |
| Date | `2026-07-12` |
| Mutates canonical truth | `false` |

This support-only packet turns the existing query-gap ledger into a concise
handoff contract between the parent frontend owner and any Pantheon BFF owner.
It does not add routes, fields, schemas, runtime behavior, frontend code, or
canonical policy. Parent `MGMT-PERF-IA-006` remains `todo` and retains all
implementation and hosted-proof responsibility.

## 1. Handoff Unit

Use one handoff unit per source-to-destination journey. Do not bundle unrelated
Cockpit, Fleet, entity, Inbox, capital, and Agora gaps into one BFF request.

| Required item | Handoff content |
|---|---|
| Journey | Named source UI, destination UI, and operator intent |
| Deployment | Frontend SHA, Pantheon BFF SHA, capture time, and viewport |
| Requested context | Exact typed entity, runtime, stage, dimension, period, as-of, item, or return keys sent |
| Fulfilled context | Response-authored stable ids, applied scope, links, source health, and timestamps/snapshots |
| First loss | First navigation or response boundary where requested and fulfilled context diverge |
| Current frontend behavior | Honest scoped, visibly unscoped, unavailable, or degraded rendering while the gap exists |
| Smallest BFF ask | One missing stable id, fulfilled-filter field, response-authored link, source-state field, snapshot, or receipt distinction |
| Negative case | Absent, invalid, stale, unauthorized, malformed, or dependency-unavailable input and its fail-closed outcome |
| Ownership | Named frontend owner, named BFF owner if split, evidence link, and parent absorption decision |

A browser URL retaining a requested value is not fulfillment evidence. Display
names, rank, row position, metric equality, nearby timestamps, fixtures, and
browser-side joins must not be used to reconstruct identity or scope.

## 2. Journey Cut Lines

| Journey | Frontend may compose when | Split only this BFF gap when | Fail-closed behavior |
|---|---|---|---|
| Cockpit -> Performance/Rankings | Destination response proves stable entity and fulfilled period/dimension | Stable id, accepted-scope metadata, or destination link is absent | Keep operational summary; disable or label formal analysis unavailable |
| Persona Fleet -> Performance/holdings | Persona/runtime identity and supported destination scope are response-authored | Fleet lacks stable binding/link or destination cannot report applied scope | Keep compact Fleet summary; do not copy formal attribution or holdings |
| Persona/Strategy detail -> Performance | Detail and attribution independently prove the same stable entity and supported period | Formal destination needs a stable id or fulfilled-period field | Keep entity summary visibly distinct and omit unsupported deep link |
| Capital Pool/Rebalance/Policy detail | Resource read returns live detail or explicit healthy-empty/unavailable source state | Detail identity, source state, or operation receipt boundary is missing | Never substitute fixtures, siblings, inferred policy, or false zeroes |
| Human Inbox -> action -> return | Item/target identity, allow-listed origin, and review state are explicit | Apply outcome lacks a durable operation receipt distinct from review state | Neutral return is allowed; never claim applied from review state alone |
| Agora <-> Management Performance | Each read independently fulfills strategy/period and exposes its own health/time | A smallest compatible stable id or applied-scope field is absent | Preserve separate execution and attribution labels; never synthesize a score or atomic snapshot |

Do not request a new aggregate endpoint merely because independent reads have
different timestamps. Keep health, freshness, loading, empty, and error state
section-local unless an operator action genuinely requires atomic semantics.

## 3. Frontend Handoff Checklist

The parent owner should record these decisions before implementation:

1. Map every entry point to an already canonical destination; do not create a
   duplicate analysis page to avoid a contract gap.
2. Use a per-destination typed query allow-list. Preserve only supported keys,
   reset pagination/continuation when scope changes, and reject unsafe return
   locations instead of reflecting arbitrary URLs.
3. Bind visible filter state to response-fulfilled context. When the adapter
   ignores or cannot prove a requested filter, render explicitly unscoped or
   unavailable rather than leaving the requested chip selected.
4. Preserve backend identifiers, lifecycle values, links, health, timestamps,
   and receipts verbatim. Missing or non-finite metrics render unavailable.
5. Keep compact entity summaries distinct from formal attribution/ranking and
   keep Agora execution performance distinct from Management attribution.
6. Exercise direct load, refresh, copied URL, back/forward, and Inbox return on
   desktop and mobile in strict-live mode without seed/fixture fallback.

## 4. BFF Ticket Template

Open a bounded Pantheon BFF ticket only when the handoff unit proves the first
missing contract element:

```text
Journey:
Deployed frontend SHA / BFF SHA / captured at:
Source route and redacted response evidence:
Destination route and redacted request/response evidence:
Requested keys:
Accepted and fulfilled keys:
First missing contract element:
Smallest requested response change:
Valid case and expected response:
Negative case and fail-closed response:
Frontend behavior until delivery:
Named BFF owner / frontend owner:
Non-goals: no generic filter expansion, aggregate endpoint, client join,
inferred identity, fixture authority, duplicate analysis page, or new mutation.
```

The BFF owner owns only the named response boundary. The frontend owner still
owns navigation serialization, section-local rendering, and honest unavailable
states. Any new mutation, authorization, receipt, registry, or governance
semantics require a separate canonical task and are outside this sidecar.

## 5. Parent Absorption And Review

Parent owner `Antigravity` should absorb only handoff units backed by deployed
strict-live evidence and route each unresolved first loss to exactly one owner.
Parent reviewer `Claude` reviews the eventual composed implementation, not this
packet as implementation evidence.

Sidecar reviewer `Antigravity` should verify:

- requested context is never treated as fulfilled context;
- every journey has a truthful fail-closed state;
- BFF requests stop at the smallest proven response gap;
- the packet does not change canonical truth or claim parent completion.

Suggested review transition:

```bash
AI_NAME=Antigravity ./scripts/ai-status.sh approve \
  MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-19 \
  "Support-only frontend/BFF handoff units and bounded ticket cut lines approved for parent absorption."
```

## 6. Preparation Evidence

- Prepared on
  `task/MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-19` from `dev`.
- Read the task-scoped brief, collaboration guide, worker-anchor and closeout
  protocols, current task/parent state, and existing parent sidecar packets.
- Did not scan `current-work.md` or the complete `ai-activity-log.jsonl`.
- Changed only this support artifact. No canonical truth, Pantheon runtime,
  BFF contract/implementation, registry/governance logic, or frontend source
  was changed.

