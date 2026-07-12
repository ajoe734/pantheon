# MGMT-PERF-IA-006 BFF Handoff Follow-up 3

| Field | Value |
|---|---|
| Parent task | `MGMT-PERF-IA-006` |
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` |
| Owner / reviewer | `Codex` / `Antigravity` |
| Helper kind | `bff_handoff_packet` |
| Scope | support-only parent readiness gate |
| Mutates canonical or runtime | `false` |

This packet is the parent owner's final readiness checkpoint for absorbing the
reviewed base handoff and follow-up 2. It does not redefine a BFF contract,
change Pantheon or `execute-plans`, approve the parent, or authorize a write.

## Ready-To-Implement Boundary

The parent may proceed without a new Pantheon BFF task when every implemented
journey can use stable identifiers already returned by the source and accepted
by the destination. The frontend route adapter may preserve compatible
`persona`, `runtime`, `strategy`, `capital_pool`, `stage`, `period`, and
`as_of` navigation context, while sending only parameters accepted by the
selected endpoint.

The implementation must retain these boundaries from the reviewed packet:

- entity pages show compact summaries and link to formal Performance or
  Rankings centers;
- Agora retains execution-performance scope and only links strategy/period
  context to Management attribution;
- composed reads expose section-local health, snapshot, empty, loading, and
  error states;
- returned identifiers and period/snapshot metadata, rather than requested URL
  values, are fulfillment evidence;
- Human Inbox return targets are allow-listed and preserve only compatible
  originating context;
- absent identity or incompatible, unhealthy, stale, fallback, or unlinked
  detail renders unavailable/degraded, never a fixture, heuristic join, or
  numeric zero.

## Stop-And-Split Gate

The parent must stop the affected journey and request a separately owned
Pantheon BFF contract task if any required transition lacks a stable source ID
that its destination accepts and honest unavailable rendering would violate an
explicit parent acceptance criterion. The gap task must record:

1. the exact source route and response identifier currently available;
2. the exact destination route and accepted identifier currently required;
3. the missing stable link or return-context capability;
4. the journeys blocked by the gap and why unavailable is insufficient;
5. focused schema, authorization, pagination/snapshot, and negative tests.

This sidecar deliberately does not choose a route, query parameter, response
field, or universal context token. Browser-side joins by display name, rank,
label, actor, timestamp, or matching text remain rejected.

## Parent Owner Evidence Matrix

| Journey | Minimum proof before parent review |
|---|---|
| Cockpit to canonical center | Copied URL, refresh, and browser-history proof preserving supported entity/stage/period context; destination shows returned snapshot and source state. |
| Persona Fleet/detail to Performance or Rankings | Stable persona/runtime identity survives navigation; compact summary is visibly distinct from formal attribution/ranking. |
| Strategy detail to Management and Agora | Strategy/period survives both links; labels prove portfolio attribution and execution performance remain different analytical scopes. |
| Human Inbox decision and return | Allow-listed return target restores originating compatible context after completion and cancellation; arbitrary external/internal URLs are rejected. |
| Capital pool and other detail panels | Healthy empty differs from unavailable, degraded, stale, fallback, and unmatched identity; no fixture is rendered as authority. |
| Multi-read center | Independent loading/error/source health/snapshot evidence; filter or dimension changes reset endpoint pagination tokens. |

The parent delivery record should name the `execute-plans` PR and merge SHA,
deployed SHA ancestry, focused adapter/navigation tests, authenticated strict-
live BFF captures for consumed routes, and hosted desktop/mobile evidence. It
must also state either the separately assigned BFF gap task or that existing
stable identifiers were sufficient.

## Compose Decision

- Parent owner `Antigravity` may absorb this readiness gate together with
  `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF.md` and
  `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`.
- Parent reviewer `Claude` evaluates the composed frontend delivery and its
  evidence; this packet is not parent approval.
- Sidecar reviewer `Antigravity` verifies only that this follow-up remains
  support-only, accurately preserves the reviewed boundary, and is useful to
  the parent implementation.

## Verification

Re-read the task brief, parent execution packet, reviewed base handoff, and
follow-up 2. Confirmed this readiness gate introduces no new endpoint or field
claim and preserves the prior query-gap, operator-journey, honest-unavailable,
and separately-owned BFF escalation boundaries. No canonical document, BFF
runtime/schema, registry, governance implementation, or frontend file was
changed. `current-work.md` and the complete `ai-activity-log.jsonl` were not
scanned.
