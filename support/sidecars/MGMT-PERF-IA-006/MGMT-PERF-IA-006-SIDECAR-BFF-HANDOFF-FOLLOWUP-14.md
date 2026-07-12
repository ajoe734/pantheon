# MGMT-PERF-IA-006 BFF Handoff Follow-up 14

| Field | Value |
|---|---|
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-14` |
| Parent task | `MGMT-PERF-IA-006` — Contextual integration |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Antigravity` / `Claude` |
| Sidecar owner / reviewer | `Codex` / `Antigravity` |
| Date | `2026-07-12` |
| Mutates canonical truth | `false` |

This support-only packet identifies BFF query gaps and gives the parent owner a
frontend integration run sheet. It does not add routes or fields, change
canonical semantics, modify `execute-plans`, or constitute parent acceptance.

## 1. Integration Boundary

The parent should link existing contextual surfaces to the canonical
Performance and Rankings centers, not build another aggregate analysis page.
Existing route families include Persona Fleet and Persona League reads,
Portfolio Book and performance-attribution reads, entity detail reads, and
governed Human Inbox/review surfaces. Their presence does not prove that one
request or snapshot spans every surface.

At each navigation hop, preserve requested context in the typed frontend URL
and verify response-fulfilled context separately. A URL value is not evidence
that the BFF honored the filter. Prefer BFF-authored stable identifiers and
links; never join by a display name, rank, metric value, or timestamp.

## 2. Entry-point Query And Handoff Matrix

| Entry point | Context to carry | BFF evidence to verify | Fail-closed frontend behavior |
|---|---|---|---|
| Cockpit card or alert | persona, runtime, strategy, pool, deployment stage, period/as-of, originating alert when supplied | Stable entity ids/links, fulfilled scope, source state, snapshot/as-of, missing-binding diagnostics | Link only to a canonical center. If identity or time context is absent, open an explicitly unscoped/unavailable view rather than guessing from the label. |
| Persona Fleet | persona and runtime; period/snapshot; market or deployment scope when supplied | `/bff/management/persona-fleet`, Persona League/detail links, response-owned health/source state and pagination | Keep the Fleet row a compact summary. Performance, holdings, ranking, evidence, and review actions must preserve available ids and must not imply one shared snapshot. |
| Persona Detail | persona, selected runtime, period/as-of | Persona/entity detail plus `/bff/management/performance-attribution/by-persona` response identity, coverage, freshness and links | Label the panel as an entity summary and deep-link to formal Performance. Do not present summary metrics as a full attribution report. |
| Strategy Detail | strategy, runtime/binding when supplied, period/as-of | Strategy detail/link and `/bff/management/performance-attribution/by-strategy`; distinguish Management attribution from Agora execution evidence | Keep contextual metrics compact. Link to formal attribution; do not merge Management and Agora scopes into one score. |
| Capital Pool detail | pool, sleeve when supplied, deployment stage, period/as-of | `/bff/capital-pools/{id}`, Portfolio Book pool/holdings reads, `/bff/management/performance-attribution/by-pool`, stable links and source state | When the detail or attribution contract is empty/unavailable, render that state. Do not fill it from fixtures or sibling rows. |
| Rebalance detail | rebalance id, pool/sleeve, originating period/snapshot | `/bff/rebalances/{id}` and only response-authored target/receipt/precondition links | Do not infer a rebalance from allocation drift or ranking. Missing detail stays unavailable and no read path becomes mutation authority. |
| Ranking Policy detail | formula/policy id, cohort/window or quarter, snapshot | `/bff/ranking-formulas/{id}` or supplied ranking links; version, eligibility and evidence identity if returned | Do not reconstruct policy authority from table columns. Missing live detail is an honest unavailable state. |
| Human Inbox | review/item id plus originating persona, strategy/pool, period/snapshot and return target | `/bff/management/human-inbox` or response-provided review link; review state, target identity, operation/receipt link if any | A decision and an applied operation are distinct. Return navigation restores the origin; absent receipt means safely non-applied. |
| Agora Strategy Performance | strategy and execution/trading-room period; runtime/broker when supplied | Agora response identity/source state and strategy link; Management attribution queried separately | Label this execution scope and keep it in Trading Room. Link across with context, but never relabel Agora execution metrics as formal Management attribution. |

Route examples above are existing families to inspect, not a request to widen
their schemas. The parent must use the exact links returned by the deployed BFF
where available.

## 3. BFF Gap Triage

| Observed gap | Required disposition |
|---|---|
| Requested persona/runtime/strategy/pool is not echoed or otherwise bound by the response | Mark fulfillment unproven. Preserve the request for navigation, but do not claim the returned data belongs to that entity. |
| Period/as-of appears only in the URL | Treat snapshot preservation as unproven; show the response's actual as-of/source state or unavailable. |
| An entity summary has metrics but no formal attribution link or stable identity | Keep it visibly contextual and open a bounded BFF contract follow-up; do not promote it to formal analysis. |
| Capital Pool, Rebalance, or Ranking Policy detail returns empty/404/unavailable | Render the explicit empty/unavailable state. Never substitute seed data, a list row, or client-derived policy. |
| Human Inbox link lacks origin/return context | Preserve an allow-listed frontend return descriptor and record the missing BFF linkage; never reconstruct the origin from review copy. |
| Review success has no operation/receipt id or retrievable link | Report the review state but mark the action safely non-applied. HTTP 2xx or a toast is not apply proof. |
| Agora and Management periods or identities cannot be aligned | Display them as separate scopes with their own timestamps; do not calculate a combined comparison. |
| A composed page has mixed source states | Retain state per section. A healthy shell cannot promote a partial, stale, degraded, unavailable, or failed query to formal/healthy. |

No integration-only aggregate endpoint is requested. A missing stable identity,
fulfilled-filter, snapshot, review, or receipt link should become a narrowly
owned backend follow-up rather than a frontend join.

## 4. Frontend Operator Journey

1. Start from a Cockpit card or alert with explicit entity and period context;
   record the initial URL and response-fulfilled context.
2. Enter Persona Fleet, then exercise performance, holdings, ranking, evidence,
   and review actions for one row. Verify persona/runtime and applicable
   period/snapshot survive every link.
3. Open Persona Detail and Strategy Detail. Confirm each remains a compact
   contextual summary and that the formal attribution deep link reaches the
   canonical Performance center without silently resetting context.
4. Open Capital Pool, Rebalance, and Ranking Policy details. Capture one live
   detail and one honest empty/unavailable result where available; verify no
   fixture authority or false zero appears.
5. Enter Human Inbox from a governed item. Record review identity and state,
   then navigate back and verify the originating entity, period/snapshot, tab,
   and decision context are restored.
6. Open Agora Strategy Performance. Confirm its execution scope remains in
   Trading Room and its cross-link preserves strategy/period while formal
   Management attribution remains separately labeled.
7. Repeat the essential path at a mobile viewport and use browser back/forward.
   Record final URLs, failed requests, console errors, and the first lost
   context value.

## 5. Parent Absorption Checklist

- Use strict live BFF mode on Pantheon-owned hosted dev and record deployed
  Pantheon/`execute-plans` revisions.
- Capture requested and fulfilled identity/time context independently at every
  hop; omit response fields that do not exist instead of synthesizing them.
- Preserve loading, healthy-empty, partial, fallback, stale, degraded,
  unavailable, unauthorized, malformed, and transport-failure states.
- Format only finite metrics; null/non-finite values remain unavailable rather
  than zero.
- Prove entity summaries are visually and behaviorally distinct from formal
  Performance/Rankings centers.
- Prove Human Inbox return navigation and any operation receipt using stable
  ids/links, or record the exact safely non-applied blocker.
- Keep Agora execution performance separate, linked, and explicitly scoped.
- Route any missing BFF contract to a bounded owner; parent reviewer `Claude`
  remains responsible for the composed verdict.

## 6. Reviewer Handoff

Reviewer `Antigravity` should confirm that this packet is support-only, maps
each legitimate entry point to a fail-closed context handoff, keeps entity
summaries and Agora distinct from formal Management analysis, and does not
invent a route, field, receipt, or canonical rule.

Recommended status transition:

```bash
AI_NAME=Antigravity ./scripts/ai-status.sh approve \
  MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-14 \
  "Support-only contextual BFF/frontend handoff approved for parent absorption."
```

## 7. Preparation Evidence

- Prepared on
  `task/MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-14` from `dev`.
- Read the task-scoped brief, collaboration guide, worker anchor and closeout
  protocols, parent execution packet, and related sidecar handoff patterns.
- Inspected relevant BFF route and contract tests without changing runtime,
  registry, governance, canonical docs, or frontend files.
- Did not scan `current-work.md` or the full `ai-activity-log.jsonl`.

## 8. Approved Closeout Evidence

- Reviewer `Antigravity` approved this support-only packet on `2026-07-12`;
  the review record is
  `support/sidecars/MGMT-PERF-IA-006/REVIEW-MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-14.md`.
- Finalization rechecked the task brief, packet, review record, branch scope,
  and diff boundary. The approved claim remains limited to parent absorption;
  parent `MGMT-PERF-IA-006` remains independently owned and evaluated.
- Verification: `git diff --check origin/dev...HEAD`; scoped diff and artifact
  inspection; `AI_NAME=Codex ./scripts/ai-status.sh show
  MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-14`.
