# OCLAW-PMEM-004 BFF Handoff Follow-up 14

**Sidecar Task ID**: `OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-14`
**Parent Task**: `OCLAW-PMEM-004`
**Parent Owner**: `Claude2`
**Sidecar Owner**: `Codex2`
**Sidecar Reviewer**: `Antigravity`
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-07-11
**Mutates Canonical**: `no`

This support-only packet provides an evidence ledger and dispatch baton for
the parent owner. It does not accept dependencies, define DTOs, modify BFF or
frontend code, or authorize frontend delivery.

## 1. Current Recommendation

Keep frontend dispatch at `defer` until the parent supplies accepted,
immutable references for `OCLAW-PMEM-002`, `OCLAW-PMEM-003`, the composed BFF
projection, and revision-matched fixtures. Route presence, proposed payloads,
credential mounts, and workspace availability are not readiness evidence.

## 2. Evidence Ledger

The parent should complete every row against the same composed BFF revision.
An absent or mutable reference keeps the dispatch gate closed.

| Evidence slice | Required immutable evidence | Failure-closed result |
|---|---|---|
| Runtime profile | Accepted authority ref, profile identity/generation, focused contract test | Runtime child is unavailable or stale; do not infer from a workspace |
| Canonical persona memory | Accepted Memory Plane ref, authorization and cross-persona denial tests, canonical IDs/scope | Distinguish unavailable/unauthorized from an available empty result |
| Materialization | Accepted OpenClaw bridge ref, attempt/result identity, source-memory join, failure fixture | Preserve canonical entries; derived cache/workspace never becomes authority |
| Provider auth and smoke | Provider identity, auth observation, separately timestamped live-smoke result and freshness rule | Auth-ready without fresh passing smoke is not usable |
| Dependency completeness | Persona dependency inventory revision and missing-dependency fixture | Incomplete/unknown inventory cannot yield definitive readiness |
| Quota and usage | Source, coverage, observation time, freshness, known/unknown semantics | Unknown/stale/unsupported is never zero or unlimited |
| Reauth | Role/MFA tests, opaque session lifecycle, advertised actions, code-entry fixture | Credential success enters verifying; only a later fresh probe changes usability |
| BFF projection | Implemented ref, bounded reason vocabulary, child-isolation and join-mismatch tests | One failed child cannot erase or falsely validate another child |
| Fixture manifest | Sanitized fixtures pinned to BFF commit/schema revision with exact commands | Frontend dispatch remains deferred if fixtures drift or invent state |

## 3. Composition Invariants

The parent review should reject composition unless all invariants hold:

1. Runtime, canonical memory, materialization, auth, live smoke, dependency
   completeness, quota/usage, and reauth remain independently observable.
2. Every cross-source join names its identity and generation; mismatches fail
   closed instead of silently merging observations.
3. Authorization occurs before private memory content, identifiers, counts, or
   other identifying metadata are returned.
4. The BFF computes provider usability from explicit evidence. The browser
   neither recomputes readiness nor invents recovery actions.
5. Observation time, freshness, completeness, source, and bounded reason
   survive the projection wherever evidence can become stale or partial.
6. Reauth start, status, code submission, cancellation, and verification use
   BFF routes only and expose only server-advertised actions.

## 4. Revision-Locked Fixture Baton

The parent should publish one sanitized fixture set covering at least:

- authorized available-empty memory;
- Memory Plane unauthorized, timeout, unreachable, and malformed responses;
- canonical memory available while materialization is failed;
- cross-persona denial without content or identity leakage;
- auth-ready with missing, stale, failed, and passing live-smoke evidence;
- incomplete dependency inventory;
- known quota beside failed smoke, plus unknown/stale quota;
- reauth awaiting code, credential success, verifying, and fresh probe result;
- mixed Codex, Claude, and OpenClaw states without pool-wide flattening;
- stale or mismatched join identities.

Each fixture must record the BFF commit and schema/fixture revision, expected
HTTP status/envelope, observation and freshness inputs, bounded reasons,
advertised actions, and an exact executable test command.

## 5. Frontend Dispatch Baton

Only after sections 2–4 pass may the parent issue this bounded instruction:

```text
Implement in ajoe734/execute-plans against Pantheon BFF commit <immutable-ref>
and fixture revision <immutable-ref>. Call Pantheon BFF routes only. Render
runtime, canonical memory, materialization, provider auth, live smoke,
dependency completeness, quota/usage, and reauth as independent sections.
Preserve source, bounded reason, observation time, freshness, completeness,
and server-advertised actions. Use BFF-computed usability. Cover every pinned
fixture in component or E2E tests and validate strict live-BFF mode before
hosted smoke.
```

The frontend task belongs in `ajoe734/execute-plans`; no frontend source or
build configuration should be added beneath the Pantheon checkout.

## 6. Parent Absorption Record

```text
Decision: absorb | absorb-with-conditions | defer
Accepted OCLAW-PMEM-002 ref: <immutable ref>
Accepted OCLAW-PMEM-003 ref: <immutable ref>
BFF implementation ref: <immutable ref>
Fixture manifest ref: <immutable ref>
Focused BFF verification: <exact commands and results>
execute-plans task/PR: <pinned ref or not-dispatched>
Unmet ledger rows: <none or list>
Residual conditions: <none or conditions retaining defer>
```

Parent owner `Claude2` owns absorption, canonical field names, implementation,
and frontend dispatch. Reviewer `Antigravity` reviews this artifact only for
scope discipline, accuracy, and handoff usefulness. Approval of this sidecar
does not approve the parent, dependencies, frontend readiness, or deployment.

