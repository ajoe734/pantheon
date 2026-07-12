# MGMT-PERF-IA-006 BFF Handoff Follow-up 18

| Field | Value |
|---|---|
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-18` |
| Parent task | `MGMT-PERF-IA-006` — Contextual integration |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Antigravity` / `Claude` |
| Sidecar owner / reviewer | `Codex` / `Antigravity` |
| Date | `2026-07-12` |
| Mutates canonical truth | `false` |

This support-only packet gives the parent owner a fillable evidence ledger for
deciding whether each contextual journey is frontend-composable or needs a
bounded Pantheon BFF ticket. It does not define or implement routes, fields,
schemas, runtime behavior, registry/governance semantics, or frontend code.

## 1. Decision Rule

A URL parameter records requested navigation context. It is not evidence that
the destination honored that context. Mark a journey `frontend-ready` only
when the deployed response supplies a stable identity and fulfilled scope (or
a response-authored link) sufficient to render the destination honestly.

Use exactly one disposition for the first gap found:

- `frontend-ready`: the deployed adapter accepts the supported key and the
  response proves the fulfilled identity/scope;
- `frontend-unscoped`: an unscoped destination is legitimate and visibly
  labelled, with no claim that the requested filter applied;
- `unavailable`: the link or section must remain unavailable because safe
  composition is impossible;
- `split-to-bff`: a smallest missing identifier, fulfilled-filter field,
  response-authored link, source-state field, snapshot, or receipt boundary is
  assigned to a Pantheon BFF owner.

Never close a gap with display-name matching, visible rank, row position,
metric equality, nearby timestamps, client fixtures, or a browser-side join.

## 2. Query-gap Evidence Ledger

Copy one row per observed source-to-destination journey. Do not fill a cell
from source inspection alone; use the deployed strict-live request/response.

| Evidence field | Value to record |
|---|---|
| Journey | Cockpit, Fleet, entity detail, Human Inbox, capital/rebalance/policy detail, or Agora source -> canonical destination |
| Deployment | Frontend SHA, Pantheon BFF SHA, capture time, desktop/mobile viewport |
| Source | Final source URL and response-authored stable ids/links available before navigation |
| Request | Destination URL and exact persona/runtime/strategy/pool/item/dimension/stage/period/as-of keys sent |
| Adapter acceptance | Exact keys accepted, rejected, or ignored by the deployed destination adapter |
| Fulfillment proof | Response-authored stable ids, fulfilled scope, links, source health, and section timestamp/snapshot |
| State | loading, healthy-empty, partial, stale, degraded, unavailable, unauthorized, malformed, or transport failure |
| Navigation proof | direct load, refresh, copied URL, back/forward, and return behavior |
| Disposition | `frontend-ready`, `frontend-unscoped`, `unavailable`, or `split-to-bff` |
| First gap | Smallest absent contract element; use `none` only with response evidence |
| Owner / evidence link | Frontend or named Pantheon BFF owner plus redacted capture/test location |

Requested values and fulfilled values must remain separate in the ledger. A
key surviving in the browser URL is not fulfillment proof.

## 3. Journey-specific Proof Bar

| Journey | Minimum response evidence | Fail-closed frontend behavior |
|---|---|---|
| Cockpit -> Performance | Stable persona/strategy/pool/runtime identity, fulfilled period or snapshot, and section source state | Keep the Cockpit summary contextual; disable or label formal analysis unavailable. |
| Persona Fleet -> Performance/holdings | Response-authored persona/runtime binding and destination links or typed filter support | Keep the compact Fleet summary; do not duplicate attribution or holdings. |
| Fleet/entity -> Rankings | Stable entity binding plus fulfilled dimension and period | Open visibly unscoped only when legitimate; otherwise make the link unavailable. |
| Persona/Strategy detail -> attribution | Fulfilled stable entity and period plus a formal-analysis destination supported by the adapter | Keep the detail summary distinct and omit the unsupported deep link. |
| Human Inbox -> Governance -> origin | Review/item/target identity, allow-listed return context, and a durable apply receipt separate from review state | Use a neutral return when needed; never claim applied without a receipt. |
| Capital Pool/Rebalance/Policy detail | Stable resource id and live detail, or explicit healthy-empty/unavailable response with source state | Render honest empty/unavailable; do not elevate fixtures, siblings, or derived policy. |
| Agora <-> Management Performance | Independently fulfilled strategy/period, labels, health, and timestamps for execution and attribution reads | Keep execution and attribution separate; do not combine scores or imply an atomic snapshot. |

## 4. BFF Ticket Cut Line

Create a bounded BFF ticket only after the ledger identifies the first missing
contract element. The ticket must include:

1. source and destination routes, deployed frontend/BFF SHAs, capture time,
   and the redacted request/response evidence;
2. exact requested keys, observed accepted keys, and the smallest missing
   stable id, fulfilled-scope field, response-authored link, health/snapshot
   field, or review-versus-operation receipt distinction;
3. one valid case and one absent, invalid, stale, or dependency-unavailable
   case with the required fail-closed result;
4. the frontend behavior while the gap remains and a named Pantheon BFF owner;
5. explicit non-goals: no generic "support all filters" expansion, duplicate
   analysis page, client join, inferred identity, fixture authority, or new
   mutation path.

If independent reads merely have different timestamps or health, keep them
section-local unless the operator journey truly requires an atomic contract.
Do not request an aggregate endpoint solely to simplify frontend rendering.

## 5. Operator Journey Run Sheet

1. Record the Wave 1 merge SHAs and prove the hosted bundle descends from the
   required destination delivery before testing that row.
2. Start from Cockpit and one Persona Fleet row. Open Performance, holdings,
   Rankings, evidence, and review destinations; log the first lost context.
3. Repeat direct load, refresh, copied URL, and back/forward. Compare requested
   and response-fulfilled context after every navigation.
4. Confirm Persona and Strategy detail summaries remain visually distinct from
   formal attribution/ranking and do not become substitute authority.
5. Exercise Capital Pool, Rebalance, and Ranking Policy detail with a live and
   an empty/unavailable case; verify no fixture or false zero appears.
6. Enter Human Inbox, complete or cancel the available review flow, and return.
   Verify restored origin context and any apply receipt independently.
7. Cross-link Agora and Management Performance with strategy/period context;
   retain separate scope labels, health, timestamps, and scoring meaning.
8. Repeat the essential journey on mobile. Capture final URLs, failed required
   requests, console errors, redirect loops, and the first context value lost.

## 6. Parent Absorption Gate

Parent `MGMT-PERF-IA-006` remains responsible for the composed implementation
and hosted proof. Before absorbing a ledger row, the parent owner should:

- confirm the corresponding Wave 1 destination is merged and deployed;
- centralize typed parse/serialize behavior using per-endpoint query
  allow-lists and reset pagination/continuation state when context changes;
- preserve backend ids, links, lifecycle values, source health, and timestamps
  verbatim, with missing or non-finite metrics shown as unavailable;
- keep multi-read state and snapshots section-local, redirects loop-free, and
  return targets allow-listed;
- attach desktop/mobile strict-live evidence with no seed/fixture fallback;
- route every unresolved first gap to one owner and one bounded ticket.

Absorbing this packet does not approve or complete the parent task.

## 7. Review And Handoff

Reviewer `Antigravity` should verify that the packet remains support-only, the
ledger separates requested from fulfilled context, every outcome fails closed,
and the BFF cut line cannot be read as a broad contract request. Parent owner
`Antigravity` decides whether to absorb it; parent reviewer `Claude` evaluates
only the eventual composed implementation and hosted evidence.

Suggested review transition:

```bash
AI_NAME=Antigravity ./scripts/ai-status.sh approve \
  MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-18 \
  "Support-only query-gap evidence ledger and BFF cut line approved for parent absorption."
```

## 8. Preparation Evidence

- Prepared on
  `task/MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-18` from `dev`.
- Read the task-scoped brief, collaboration guide, worker-anchor and closeout
  protocols, parent execution packet, and immediately preceding handoffs.
- Used `AI_NAME=Codex` for task status and did not scan `current-work.md` or the
  complete `ai-activity-log.jsonl`.
- Changed only this support artifact. No canonical truth, Pantheon runtime,
  BFF route/schema, registry, governance implementation, or frontend source
  was changed.

## 9. Finalization Evidence

- Reviewer `Antigravity` approved the support-only packet on `2026-07-12`;
  the approval record is
  `REVIEW-MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-18.md`.
- Closeout preserves the approved boundary: this packet is evidence and ticket
  shaping for parent absorption, not a claim that any query is fulfilled or
  that any BFF/frontend implementation exists.
- Focused closeout verification: `git diff --check`; phrase checks for the four
  dispositions, requested-versus-fulfilled separation, bounded BFF ticket
  scope, parent responsibility, and canonical non-mutation.
- Parent `MGMT-PERF-IA-006` remains responsible for implementation, deployed
  strict-live desktop/mobile proof, and any bounded BFF gap tickets.
