# MGMT-PERF-IA-006 BFF Handoff Follow-up 28

| Field | Value |
|---|---|
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-28` |
| Parent task | `MGMT-PERF-IA-006` — Contextual integration |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Antigravity` / `Claude` |
| Sidecar owner / reviewer | `Codex2` / `Antigravity` |
| Date | `2026-07-12` |
| Mutates canonical truth | `false` |

This support-only packet gives the parent owner a final evidence-reconciliation
card for BFF query gaps, operator journeys, and frontend composition. It does
not define routes, query keys, response fields, schemas, runtime behavior,
frontend code, or canonical truth.

## 1. Reconciliation Gate

Reconcile only evidence from the same hosted run: Pantheon-owned frontend
origin, served frontend SHA, BFF SHA, authenticated role, strict-live settings,
and capture time. The served frontend SHA must descend from the destination
implementation being evaluated. A merged PR, retained URL parameter, fixture,
mock, fallback response, or successful source-page render is not destination
journey proof.

Evaluate each distinct source action separately. Performance, holdings,
ranking, evidence, review, and execution-quality actions from the same entity
are not interchangeable.

## 2. Source-to-Destination Evidence Card

Complete one card for Cockpit, Persona Fleet, Persona Detail, Strategy Detail,
Human Inbox, Capital Pool or policy detail, and Agora actions that the parent
claims to compose.

```text
Journey and source action:
Hosted origin / frontend SHA / BFF SHA / captured at:
Authenticated role and strict-live proof:
Source route and redacted response:
Source-authored stable ids, stage, period, and snapshot context:
Typed navigation request and allow-listed return target:
Destination route and redacted request/response:
Destination-fulfilled ids, scope, period, snapshot, and source health:
Direct load / refresh / copied URL / back-forward result:
Applicable Human Inbox completion / cancellation result:
Desktop / mobile evidence paths:
Healthy-empty / unavailable / stale / fallback / invalid / unauthorized result:
First evidence divergence:
Disposition and owner:
```

Use exactly one disposition:

- `absorb`: response-authored identity and supported scope survive the complete
  journey;
- `visibly-unscoped`: the destination explicitly shows that requested context
  is unsupported;
- `honest-unavailable`: absent, invalid, stale, unauthorized, incompatible, or
  dependency-down data stays unavailable without an inferred join or fixture;
- `split-to-bff`: same-run strict-live captures isolate the first missing
  response boundary and truthful frontend behavior cannot meet acceptance; or
- `proof-pending`: the implementation may exist, but comparable hosted evidence
  is incomplete.

Labels, display names, rank, row position, matching metric values, nearby
timestamps, and URL retention do not prove stable identity or fulfilled scope.
Reset pagination or continuation whenever effective endpoint or scope changes.

## 3. First-divergence Rule

Locate the first boundary where source-authored context stops being
response-supported:

1. source response lacks a stable destination identity or compatible scope;
2. the typed navigation adapter drops or rewrites supported context;
3. the destination request cannot express a supported filter;
4. the destination response does not confirm fulfilled identity, scope,
   snapshot, or health; or
5. return navigation loses an allow-listed origin or supported decision
   context.

Only cases 1, 3, or 4 may justify `split-to-bff`, and only after deployed
captures show that the existing BFF boundary is the constraint. Cases 2 and 5
belong to `execute-plans`. Unsupported scope that the UI labels honestly is not
automatically a defect.

## 4. Minimal BFF Split Packet

For `split-to-bff`, attach the evidence card plus:

```text
Parent acceptance statement blocked:
First missing response-supported boundary:
Smallest response capability requested (no proposed field name required):
Valid case and expected result:
Absent / invalid / stale / unauthorized / dependency-down cases:
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
  truth; one healthy response cannot mask another degraded response.
- Missing and non-finite values remain unavailable, never false zero.
- Human Inbox completion does not imply an applied operation without a
  separate durable operation receipt.
- Desktop and mobile preserve identity, scope, period, source state, primary
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
  MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-28 Antigravity \
  "Support-only evidence reconciliation and first-divergence packet ready for review."
```

## 7. Preparation Evidence

- Prepared on
  `task/MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-28` from `dev`.
- Read the task-scoped brief, collaboration guide, worker-anchor and closeout
  protocols, umbrella handoff, and immediately preceding support packets.
- Did not scan `current-work.md` or the complete `ai-activity-log.jsonl`.
- Changed only this support artifact. No canonical truth, Pantheon BFF/runtime
  code, schema, registry/governance implementation, or frontend source changed.
