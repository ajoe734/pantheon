# MGMT-PERF-IA-006 BFF Handoff Follow-up 17

| Field | Value |
|---|---|
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-17` |
| Parent task | `MGMT-PERF-IA-006` — Contextual integration |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Antigravity` / `Claude` |
| Sidecar owner / reviewer | `Codex` / `Antigravity` |
| Date | `2026-07-12` |
| Mutates canonical truth | `false` |

This support packet converts contextual-navigation observations into bounded
BFF or frontend work. It defines no route, schema, registry, governance, or
runtime truth and does not modify `execute-plans`. Parent owner `Antigravity`
decides whether to absorb any row after its Wave 1 destination is merged and
deployed.

## 1. Evidence-first Classification

For each source-to-destination journey, capture the requested context and the
destination response separately. A query parameter proves navigation intent;
only a response-authored identifier, fulfilled-filter field, link, or snapshot
proves fulfillment.

Classify the first observed gap with exactly one disposition:

| Observation | Owner lane | Required handoff | Safe frontend behavior |
|---|---|---|---|
| Destination already accepts the key and returns fulfilled scope | Frontend | Typed parse/serialize rule and refresh/back-forward test | Preserve the key verbatim and render response scope. |
| Destination rejects or ignores a required stable identifier | Pantheon BFF | Named endpoint, missing query key, expected stable-id/fulfilled-scope response, negative test | Open unscoped only when clearly labelled; otherwise mark the link unavailable. |
| Required entity or return link is absent from the response | Pantheon BFF | Named response object and missing response-authored link; no client join | Omit the deep link or provide an allow-listed neutral return destination. |
| Multiple reads disagree in snapshot or health | BFF only if an atomic contract is required; otherwise frontend | State whether atomicity is required and name the incompatible timestamps/health fields | Keep sections and timestamps independent; never synthesize a shared snapshot. |
| Response is honestly empty, partial, stale, or degraded | Frontend | State-to-UI mapping and strict-live evidence | Render that state locally; do not substitute fixtures, siblings, or false zeroes. |
| Mutation review exists but operation receipt is absent | Governance/BFF owner | Separate review decision identity from durable operation receipt identity | Show reviewed/pending safely, never applied or successful. |

No gap may be closed by display-name matching, row position, visible rank,
metric equality, nearby timestamps, or browser-side joins.

## 2. Context And Query Handoff Matrix

| Journey | Context to request | Response evidence required | Fail-closed result when absent |
|---|---|---|---|
| Cockpit -> Performance Center | persona, strategy, pool, runtime, period/as-of when available | Stable ids plus accepted/fulfilled scope and section timestamp | Disable or label formal analysis unavailable; keep the Cockpit summary contextual. |
| Persona Fleet -> Performance/holdings | persona and runtime, period/as-of | Response-authored persona/runtime identity and links; destination-local source state | Keep compact Fleet data; do not duplicate formal attribution or holdings. |
| Fleet/entity -> Rankings Center | stable entity id, dimension, period | Accepted dimension/period and response-authored entity scope | Open visibly unscoped or unavailable; never filter by name or displayed rank. |
| Persona/Strategy detail -> attribution | stable persona or strategy id, period | Fulfilled entity and period plus formal-analysis link or typed destination support | Keep a compact entity summary and omit the formal link. |
| Human Inbox -> Governance -> origin | item/target id, allow-listed origin descriptor | Review state, target identity, safe return target, and separate apply receipt when applicable | Preserve review truth; use a neutral return and show no applied claim. |
| Capital Pool/Rebalance/Policy detail | stable resource id, stage/as-of | Live detail or explicit empty/unavailable response with source state | Render unavailable/healthy-empty; never elevate fixtures or client-derived policy. |
| Agora <-> Management Performance | strategy id and period | Independently fulfilled identifiers, labels, health, and timestamps for execution and attribution reads | Retain separate scopes; do not combine scores or imply atomicity. |

## 3. Minimal BFF Gap Ticket Template

When a row is classified to Pantheon BFF, the parent should create a bounded
ticket containing:

- source and destination routes, deployed frontend SHA, BFF SHA, and capture
  time;
- exact requested keys and the endpoint's observed accepted keys;
- smallest missing stable identifier, fulfilled-scope field, response-authored
  link, source-state field, snapshot, or receipt distinction;
- one successful response example and one absent/invalid/stale dependency
  example, redacted as needed;
- required fail-closed status and frontend rendering while the gap remains;
- explicit non-goals: no new duplicate analysis page, client-side join,
  inferred identity, fixture authority, or mutation path.

A ticket should request the smallest contract addition needed for one journey,
not a generic "support all filters" expansion.

## 4. Frontend Handoff Checklist

- Centralize typed parsing and serialization for only endpoint-accepted query
  keys; discard unknown keys safely.
- Reset pagination or continuation state whenever entity, dimension, period,
  stage, snapshot, or destination changes.
- Preserve backend ids, lifecycle values, links, health, and timestamps
  verbatim. Treat null and non-finite metrics as unavailable.
- Keep loading, empty, partial, stale, degraded, unauthorized, malformed, and
  transport-failure states section-local for multi-read screens.
- Keep redirects loop-free and return destinations allow-listed.
- Verify direct load, refresh, copied URL, back/forward, desktop, and mobile.
- Record required-request failures and confirm strict-live mode never falls
  back to seed or fixture authority.

## 5. Parent Composition Gate

The parent remains `todo` and depends on `MGMT-PERF-IA-003`,
`MGMT-PERF-IA-004`, and `MGMT-PERF-IA-005`. Before accepting a matrix row,
the parent must record the corresponding Wave 1 merge SHA, prove the hosted
bundle descends from it, and validate the actual destination behavior. A
deployed Rankings row does not prove Performance, Governance, return-link, or
detail-contract readiness.

For hosted acceptance, exercise at least one successful and one
empty/unavailable case per relevant destination. Record final URLs, requested
versus fulfilled context, response source state and timestamps, console
errors, and failed required requests. Agora execution performance must remain
visibly separate from Management attribution throughout the journey.

## 6. Review And Ownership

Reviewer `Antigravity` should verify that this artifact is support-only, each
gap has a single owner lane and fail-closed behavior, and no route or contract
is represented as implemented without runtime evidence. Parent reviewer
`Claude` reviews only the eventual composed frontend and hosted evidence.

Suggested transition after review:

```bash
AI_NAME=Antigravity ./scripts/ai-status.sh approve \
  MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-17 \
  "Support-only BFF gap-routing and frontend handoff matrix approved for parent absorption."
```

## 7. Preparation Evidence

- Prepared on
  `task/MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-17` from `dev`.
- Read the task-scoped brief, collaboration guide, worker-anchor and closeout
  protocols, parent execution packet, and the immediately preceding handoff.
- Used `AI_NAME=Codex` for task status; did not scan `current-work.md` or the
  complete `ai-activity-log.jsonl`.
- Changed only this support artifact. No canonical truth, Pantheon runtime,
  BFF schema/route, registry, governance implementation, or frontend source
  was changed.

