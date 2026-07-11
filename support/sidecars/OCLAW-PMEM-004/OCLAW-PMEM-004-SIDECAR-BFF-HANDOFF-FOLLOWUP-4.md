# OCLAW-PMEM-004 BFF Handoff Follow-up 4

**Sidecar Task ID**: `OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-4`  
**Parent Task**: `OCLAW-PMEM-004`  
**Parent Owner**: `Claude2`  
**Sidecar Owner**: `Codex`  
**Sidecar Reviewer**: `Antigravity`  
**Helper Kind**: `bff_handoff_packet`  
**Generated**: 2026-07-11  
**Mutates Canonical**: `no`

This support-only packet records the current BFF-to-frontend composition seam.
It does not select a canonical DTO, change a route, modify Memory Plane truth,
or edit either runtime repository. The parent owner decides what to absorb.

## 1. Current BFF Inventory

The parent implementation can compose existing BFF seams instead of creating
browser-to-downstream calls:

| Concern | Current Pantheon BFF seam | Handoff consequence |
|---|---|---|
| Persona routing | `GET /bff/personas/{persona_id}/runtime-profile` | Keep routing independently renderable when memory is unavailable. |
| Persona memory | `GET /bff/personas/{persona_id}/memory` | Replace the optional read-store behavior before treating an empty item list as canonical. |
| Provider evidence | `GET /bff/assistant/providers?auth_probe=true` | Preserve auth and probe observations; do not collapse them into a browser-computed ready flag. |
| Usage provenance | `GET /bff/assistant/providers/usage-summary` | Missing quota source remains unknown, never numeric zero. BFF-observed usage is not total provider usage. |
| Reauthentication | `POST /bff/assistant/provider/reauth`, `GET /bff/assistant/provider/reauth/{session_id}`, `POST /bff/assistant/provider/reauth/{session_id}/code` | Render only actions advertised by the returned lifecycle; re-probe after credential completion. |

The route inventory is implementation evidence, not proof that the parent
acceptance criteria are already satisfied.

## 2. Blocking Query Gap

The highest-risk join remains the persona memory projection. A missing optional
reader must not produce the same success shape as a canonical Memory Plane
response containing no authorized entries. The parent DTO may choose its own
envelope, but it must retain all of these meanings:

| Observation | Required operator meaning |
|---|---|
| Memory Plane returned an authorized empty result | Source available; zero entries is valid. |
| Memory Plane is not configured, unreachable, or timed out | Source unavailable with a stable sanitized reason; not an empty state. |
| Canonical entries exist but materialization evidence is absent or failed | Entries remain visible; derived cache/materialization is unknown or failed separately. |
| Requested private entry belongs to another persona | No content or cross-persona metadata leakage. |

Canonical entry identity, scope, source, and observation/retrieval time should
survive the BFF projection. Workspace presence or mount readiness must never be
used as canonical-memory evidence.

## 3. Provider Pool Join Rules

The BFF, rather than the frontend, should compute the headline usability state.
Its inputs remain independently observable:

- credential/auth state;
- latest live smoke result and observation time;
- quota source and limited observed-usage coverage;
- persona dependency inventory plus a completeness indicator;
- reauth lifecycle and allowed next action.

These guards prevent false readiness:

1. Auth success with a failed, stale, or missing live smoke is not usable.
2. Reauth completion without a fresh probe is verifying or unknown, not usable.
3. Partial persona inventory cannot produce a definitive zero dependency count.
4. Missing quota evidence cannot produce a zero quota or zero usage claim.
5. Mounted workspace/config state is diagnostic context only, not provider
   usability.

## 4. Frontend Handoff Fixtures

The `execute-plans` implementation should consume BFF fixtures for these
operator-visible states; it must not call Memory Plane, OpenClaw adapter, or a
provider directly.

| Fixture | Expected rendering |
|---|---|
| canonical memory available with zero items | Valid “no memories” empty state. |
| canonical memory unavailable | Source-error state with sanitized reason and retry affordance. |
| canonical memory available, materialization failed | Memory remains visible; separate cache warning. |
| auth ready, live smoke failed | Provider degraded/unusable; auth remains visibly ready. |
| live smoke stale or absent | Usability unknown/verifying with observation time. |
| quota source absent | Quota unavailable; no invented numeric value. |
| dependency scan partial | Dependency count labeled incomplete/non-definitive. |
| reauth awaiting code | Code entry shown; unrelated actions disabled. |
| reauth complete before fresh probe | Verifying state until new probe evidence arrives. |

## 5. Suggested Parent Test Boundary

Focused BFF contract tests should prove:

- canonical empty and source-unavailable memory responses are distinct;
- cross-persona private memory is not disclosed;
- materialization failure does not erase canonical memory;
- provider auth cannot override failed or stale smoke evidence;
- quota and dependency completeness remain explicit;
- code-required reauth is sanitized and post-reauth readiness requires a fresh
  probe.

Frontend component tests should then assert copy, action visibility, and
degraded-state rendering against those BFF fixtures. A browser snapshot alone
does not prove the source or authorization boundary.

## 6. Parent Absorption Checklist

- [ ] Consume accepted dependency-lane evidence rather than copying or
  redefining Memory Plane/materialization contracts.
- [ ] Remove the optional-reader false-empty behavior from persona memory.
- [ ] Keep runtime, canonical memory, and materialization availability
  independent.
- [ ] Return BFF-computed provider usability while preserving its evidence.
- [ ] Hand stable fixtures to `ajoe734/execute-plans`; do not materialize
  frontend source inside Pantheon.
- [ ] Validate the hosted frontend in live/strict BFF mode with safe write
  defaults.

## 7. Non-Claims and Handoff

This packet does not claim that `OCLAW-PMEM-002` or `OCLAW-PMEM-003` is
accepted, that current routes satisfy the parent acceptance criteria, that a
specific response schema is canonical, or that provider usability has been
proved. `Claude2` owns the parent implementation and absorption decision.
`Antigravity` reviews this sidecar only for accuracy, scope, and non-canonical
boundary discipline.
