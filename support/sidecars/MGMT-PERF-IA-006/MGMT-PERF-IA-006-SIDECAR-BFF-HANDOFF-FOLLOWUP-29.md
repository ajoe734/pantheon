# MGMT-PERF-IA-006 BFF Handoff Follow-up 29

| Field | Value |
|---|---|
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-29` |
| Parent task | `MGMT-PERF-IA-006` — Contextual integration |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Antigravity` / `Claude` |
| Sidecar owner / reviewer | `Codex2` / `Antigravity` |
| Date | `2026-07-12` |
| Mutates canonical truth | `false` |

This support-only packet gives the parent owner a decision-ready manifest for
the BFF query gaps, operator journeys, and frontend composition evidence. It
does not define routes, query keys, response fields, schemas, runtime behavior,
frontend code, or canonical truth.

## 1. Decision Gate

Decide each real source action independently and only from one comparable
hosted run. Record the Pantheon-owned frontend origin, served frontend SHA,
BFF SHA, authenticated role, strict-live configuration, and capture time. The
served frontend SHA must contain the destination implementation under test.

A merged PR, source-page success, retained URL parameter, fixture, mock,
fallback response, label match, rank, row position, similar metric, or nearby
timestamp is not proof that the destination fulfilled identity or scope.

## 2. Parent Decision Manifest

Complete one row per distinct action. Persona Fleet performance, holdings,
ranking, evidence, and review actions are separate rows.

| Journey / source action | Source response evidence | Navigation request | Destination response evidence | Return and negative-state evidence | Decision / owner |
|---|---|---|---|---|---|
| Cockpit card or alert | Parent capture | Parent capture | Parent capture | Parent capture | Parent decision |
| Persona Fleet action | Parent capture | Parent capture | Parent capture | Parent capture | Parent decision |
| Persona Detail formal-analysis link | Parent capture | Parent capture | Parent capture | Parent capture | Parent decision |
| Strategy Detail attribution or Agora link | Parent capture | Parent capture | Parent capture | Parent capture | Parent decision |
| Human Inbox review action | Parent capture | Parent capture | Parent capture | Parent capture | Parent decision |
| Capital Pool, Rebalance, or Ranking Policy detail | Parent capture | Parent capture | Parent capture | Parent capture | Parent decision |
| Agora execution-performance link | Parent capture | Parent capture | Parent capture | Parent capture | Parent decision |

Each evidence set must identify source-authored stable ids, stage, period, and
snapshot context; the typed navigation and allow-listed return target; the
destination request; and destination-returned identity, fulfilled scope,
snapshot, and source health. Exercise direct load, refresh, copied URL,
back/forward, and applicable Human Inbox completion and cancellation on desktop
and mobile.

Use exactly one decision:

- `absorb`: response-authored identity and supported scope survive the whole
  journey;
- `visibly-unscoped`: the destination explicitly shows that requested context
  is unsupported;
- `honest-unavailable`: absent, invalid, stale, unauthorized, incompatible, or
  dependency-down data remains unavailable without inferred joins or fixtures;
- `split-to-bff`: same-run strict-live captures isolate the first missing
  response-supported boundary and truthful frontend behavior cannot satisfy
  parent acceptance; or
- `proof-pending`: comparable hosted evidence is incomplete.

Reset pagination or continuation whenever the effective endpoint or scope
changes.

## 3. First-divergence And Ownership Rule

Record the first point where response-supported context is lost:

1. the source response lacks a stable destination identity or compatible
   scope;
2. the typed frontend navigation drops supported context;
3. the destination request cannot express the supported scope;
4. the destination response does not confirm fulfilled identity, scope,
   snapshot, or health; or
5. return navigation loses its allow-listed origin or decision context.

Cases 2 and 5 belong to `execute-plans`. Cases 1, 3, or 4 may become a BFF
split only when deployed evidence proves the existing response boundary is the
constraint. Unsupported scope that the UI labels honestly is not automatically
a defect.

## 4. Minimal BFF Handoff

For every `split-to-bff`, attach this bounded packet:

```text
Blocked parent acceptance statement and journey:
Hosted origin / frontend SHA / BFF SHA / captured at:
Authenticated role and strict-live proof:
Source route and redacted response:
Source-authored ids, scope, period, snapshot, and health:
Destination route and redacted request/response:
Requested versus response-fulfilled context:
First missing response-supported boundary:
Smallest response capability requested (no field name required):
Valid result and absent / invalid / stale / unauthorized / dependency-down cases:
Fail-closed frontend behavior pending delivery:
BFF owner / reviewer and frontend compose owner:
Explicit non-goals:
```

Non-goals include a universal context token, generic filter expansion,
browser-side joins, convenience aggregates, fixture authority, duplicate
formal analysis, new mutation semantics, or a canonical-contract change
inferred from this sidecar.

## 5. Operator Truthfulness Checks

- Compact Persona and Strategy summaries remain visibly distinct from formal
  attribution and ranking.
- Agora execution quality remains separate from Management attribution.
- Multi-read pages retain section-local loading, error, health, and snapshot
  truth; one healthy response cannot mask a degraded dependency.
- Missing and non-finite values remain unavailable, never false zero.
- Healthy empty, unavailable, stale/fallback, invalid, unauthorized, and
  dependency-down states remain distinguishable.
- Human Inbox completion does not imply an applied operation without a
  separate durable operation receipt.
- Desktop and mobile retain identity, scope, period, source state, primary
  metric, and navigation action before secondary detail.

## 6. Ownership And Handoff

Parent owner `Antigravity` owns same-run hosted capture, selective absorption
into `execute-plans`, and assignment of any evidence-backed BFF split. Parent
reviewer `Claude` reviews the composed parent delivery. Sidecar reviewer
`Antigravity` reviews only whether this packet is accurate, useful,
fail-closed, and support-only; approval does not approve or complete the
parent.

Suggested transition:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh handoff \
  MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-29 Antigravity \
  "Support-only decision manifest and bounded BFF split packet ready for review."
```

## 7. Preparation Evidence

- Prepared on
  `task/MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-29` from `dev`.
- Read the task-scoped brief, collaboration guide, worker-anchor and closeout
  protocols, parent execution packet, umbrella handoff, current task state,
  and immediately preceding support packets.
- Did not scan `current-work.md` or the complete `ai-activity-log.jsonl`.
- Changed only this support artifact. No canonical truth, Pantheon BFF/runtime
  code, schema, registry/governance implementation, or frontend source changed.

## 8. Finalization Evidence

- Sidecar reviewer `Antigravity` recorded `PASS` and returned the task to
  `Codex2` for owner finalization in
  `docs/reviews/2026-07-12-mgmt-perf-ia-006-sidecar-bff-handoff-followup-29-antigravity-review.md`.
- Final scope verification used `git diff --check origin/dev...HEAD` and
  `git diff --name-only origin/dev...HEAD`; the reviewed branch contains only
  this support packet and its task-scoped review note.
- The approved packet remains advisory support for parent owner `Antigravity`.
  It does not approve the parent task, mutate canonical truth, or claim that a
  BFF/frontend capability has been implemented or deployed.
