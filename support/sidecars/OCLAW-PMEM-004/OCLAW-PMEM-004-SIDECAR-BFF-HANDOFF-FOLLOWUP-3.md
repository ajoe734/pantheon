# OCLAW-PMEM-004 BFF Handoff Follow-up 3

**Sidecar Task ID**: `OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-3`
**Parent Task**: `OCLAW-PMEM-004`
**Parent Owner**: `Claude2`
**Sidecar Owner**: `Codex2`
**Sidecar Reviewer**: `Antigravity`
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-07-11
**Mutates Canonical**: `no`

This support-only follow-up turns the earlier gap analysis into a bounded
composition and test handoff. It does not choose a canonical DTO, add routes,
change Memory Plane or runtime-profile truth, or modify Pantheon/frontend
runtime code. The parent owner decides what to absorb.

## 1. Parent Decision Summary

The narrow implementation seam is two BFF-owned projections, backed only by
accepted upstream evidence:

1. persona detail composition: existing runtime profile + canonical Memory
   Plane result + accepted materialization evidence;
2. provider pool composition: provider auth/probe + quota/usage provenance +
   persona dependency completeness + reauth lifecycle.

The parent should not wait for a frontend join to define truth. The BFF must
preserve source availability and compute usability; the browser only renders
the returned states.

## 2. Route-to-Field Composition Map

| Output group | Existing input seam | Parent-owned join rule | Must not infer |
|---|---|---|---|
| `runtime_profile` | `GET /bff/personas/{persona_id}/runtime-profile`; `build_persona_runtime_profile(...)` | Reuse unchanged or embed its projection with its error state. | Browser-selected fallback or model validity. |
| `memory.entries` | Replace the optional reader used by `GET /bff/personas/{persona_id}/memory` with the accepted Memory Plane facade from the dependency lane. | Preserve canonical entry ID, scope, source, retrieval time, and authorization result. | Empty list when the source was not reached. |
| `memory.materialization` | Accepted `OCLAW-PMEM-003` bridge/result evidence. | Join by persona and generation/source references; expose missing evidence as unknown. | Success from workspace or mount presence. |
| `provider.auth` and `provider.live_smoke` | `GET /bff/assistant/providers?auth_probe=true` through `OpenClawOpsClient.list_assistant_providers(...)`. | Keep credential/auth and live probe observations distinct, including timestamps and sanitized reasons. | Usability from auth alone. |
| `provider.quota` and `observed_usage` | `GET /bff/assistant/providers/usage-summary`. | Preserve quota `source` and limited BFF-observed coverage. | Total provider usage or zero quota from missing data. |
| `persona_dependencies` | Accepted persona runtime-profile inventory. | Join primary/fallback provider refs server-side; return `complete: false` when inventory is partial. | Definitive zero dependencies from an incomplete scan. |
| `reauth` | Start/status/code routes under `/bff/assistant/provider/reauth`. | Normalize lifecycle and allowed next action; after success require a fresh probe. | Readiness from successful credential flow. |

## 3. Availability Contract the Parent Tests Should Lock

The exact envelope name remains parent-owned, but these cases must be
observably distinct:

| Source result | Required projection meaning | HTTP/UI consequence |
|---|---|---|
| Memory Plane answered with no authorized entries | `status: available`, zero items, source identified | Valid empty-state copy. |
| Memory Plane not configured/unreachable/timed out | `status: unavailable`, stable reason, source identified | Source-error copy; never “no memories”. |
| Canonical entries available, materialization absent | memory available; materialization `unknown` | Show canonical entries and a separate cache-evidence warning. |
| Auth ready, fresh smoke passed | independent evidence both positive | May be usable only when BFF returns usable. |
| Auth ready, smoke failed/stale/missing | auth remains ready; usability degraded/unknown | Never show provider usable. |
| Reauth succeeded, probe not rerun | reauth succeeded; usability verifying/unknown | Poll or request fresh probe before promotion. |
| Dependency inventory partial | `complete: false`, count non-definitive | UI labels incomplete; does not show zero dependents. |

Stable machine reasons should be bounded values such as
`memory_plane_not_configured`, `memory_plane_unreachable`,
`memory_plane_timeout`, `live_smoke_failed`, `live_smoke_stale`, and
`dependency_inventory_incomplete`. These are suggested support vocabulary,
not a canonical enum declaration.

## 4. Focused Acceptance Fixtures

The parent can cover the risky joins with a small matrix rather than broad UI
snapshots:

| Fixture | Inputs | Required assertions |
|---|---|---|
| canonical empty | Memory Plane success with `[]` | available, total zero, no unavailable reason |
| canonical unavailable | client timeout/error | unavailable reason present; not rendered as empty success |
| scope denial | entry belongs to another private persona scope | no entry leakage; denial/not-found semantics follow accepted Memory Plane contract |
| cache failure | canonical entry exists; materialization failed | entry remains visible; cache status and sanitized reason separate |
| false-ready guard | auth ready; live smoke failed | usability is not usable |
| stale probe guard | auth ready; last smoke outside accepted freshness | usability unknown/degraded; timestamp retained |
| quota unknown | quota source not configured | numeric quota fields remain null; UI says unavailable |
| incomplete dependency join | only partial runtime-profile inventory loads | completeness false; no definitive zero |
| code-required reauth | status advertises `awaiting_code` | code action is allowed and other steps remain disabled |
| post-reauth verification | reauth succeeds before fresh probe | state is verifying, not usable |

Frontend component tests in `ajoe734/execute-plans` should consume these BFF
fixtures. Browser code must not call Memory Plane, the OpenClaw adapter, or a
provider directly.

## 5. Operator Journey Handoff

### Persona detail

1. Load runtime and memory projections independently so one unavailable source
   does not erase the other.
2. Label canonical memory and derived workspace materialization separately.
3. Render available-empty, source-unavailable, and materialization-failed as
   three different states.
4. Keep canonical IDs and generation timestamps visible enough for operator
   troubleshooting without exposing private content outside authorization.

### LLM Auth / provider pool

1. Render auth, live smoke, quota provenance, persona dependencies, and reauth
   as separate fields per provider.
2. Use only BFF-computed usability for the headline state.
3. Start and poll reauth through the BFF; show code entry only when advertised.
4. After reauth succeeds, show verifying until a new live probe completes.
5. Keep sanitized failure reason and observation time available for diagnosis.

## 6. Parent Absorption Checklist

- [ ] Consume accepted `OCLAW-PMEM-002` and `OCLAW-PMEM-003` outputs rather
  than copying their contracts into BFF.
- [ ] Remove the persona-memory optional-reader false-empty behavior.
- [ ] Keep runtime, canonical memory, and materialization availability
  independent.
- [ ] Keep provider auth, smoke, quota provenance, dependency completeness,
  reauth, and usability independent.
- [ ] Add the focused BFF fixtures above before handing a stable DTO to the
  frontend repository.
- [ ] Ensure frontend live-dev validation uses strict BFF fallback and no
  direct downstream calls.

## 7. Non-Claims and Handoff

This packet does not claim dependency completion, approve endpoint names or a
schema, implement either repository, prove provider usability, or make
workspace materialization canonical. `Claude2` owns the parent implementation
and decides whether to absorb this support packet. `Antigravity` reviews only
that the sidecar is accurate, bounded, and non-canonical.
