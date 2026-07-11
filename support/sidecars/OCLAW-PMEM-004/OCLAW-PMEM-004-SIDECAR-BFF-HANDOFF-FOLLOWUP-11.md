# OCLAW-PMEM-004 BFF Handoff Follow-up 11

**Sidecar Task ID**: `OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-11`
**Parent Task**: `OCLAW-PMEM-004`
**Parent Owner**: `Claude2`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Antigravity`
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-07-11
**Mutates Canonical**: `no`

This support-only follow-up narrows the parent handoff to query ownership,
evidence deltas, and a dispatch gate. It does not define canonical fields,
approve dependencies, change BFF or frontend code, or promote current route
behavior to accepted contract truth.

## 1. Current Decision

**Keep frontend dispatch deferred.** Durable task state records
`OCLAW-PMEM-001`, `OCLAW-PMEM-002`, and `OCLAW-PMEM-003` as `todo`; the parent
is also `todo` and depends on `OCLAW-PMEM-002` and `OCLAW-PMEM-003`. Existing
route seams therefore prove only that integration points exist. They do not
prove that canonical authority, join keys, degradation semantics, or fixtures
have been accepted and composed.

The parent may change this decision only after recording immutable accepted
dependency refs, an implemented BFF/fixture ref, focused verification, and an
`ajoe734/execute-plans` task pinned to those refs.

## 2. Query Ownership Matrix

The BFF must own the operator-facing joins. The browser renders these answers
and must not reconstruct them from unrelated endpoints.

| Operator query | Current BFF seam | Parent-owned missing result | Must remain independent |
|---|---|---|---|
| Where will the persona run? | `GET /bff/personas/{persona_id}/runtime-profile` | Accepted routing/profile authority, source generation, validation result, and bounded failure | Memory and provider availability |
| Can canonical memory be read now? | `GET /bff/personas/{persona_id}/memory` | Authorized Memory Plane result with available-empty versus unavailable semantics, canonical IDs, scope, and observation time | Runtime routing and materialization |
| Was canonical memory materialized into OpenClaw? | No accepted aggregate projection recorded | Attempt/result identity, source-entry lineage, generation, observation time, and bounded failure | Canonical entries remain visible on failure |
| Can a provider serve work now? | `GET /bff/assistant/providers?auth_probe=true` | BFF-computed usability from auth plus fresh live smoke plus complete required dependencies | Quota and configured/mounted credentials |
| Is provider capacity known? | `GET /bff/assistant/providers/usage-summary` | Value, source/provenance, coverage, observation time, freshness, and unknown/error state | Unknown never becomes zero, unlimited, or healthy |
| What recovery action is allowed? | Reauth start/status/code routes exist | Bounded server-advertised actions with role/MFA requirements and sanitized reasons | Browser does not invent retry or code-entry actions |
| Did reauth restore usability? | Reauth lifecycle routes exist | Successful credential flow transitions to `verifying` until a subsequent fresh live probe passes | Reauth completion alone is not provider readiness |

For every joined child, preserve its own status, bounded reason, source,
`observed_at`, freshness, and completeness. A summary must not flatten an
unknown, stale, partial, unavailable, or failed child into ready.

## 3. Evidence Delta the Parent Must Close

Route presence and isolated delegation/security tests are reusable, but these
cross-surface proofs are still required before handoff:

1. Pin accepted `OCLAW-PMEM-002` and `OCLAW-PMEM-003` revisions and identify
   their authority and join keys. If they do not compose, return the precise
   gap to the dependency owner rather than inventing a BFF-local identity.
2. Prove private persona authorization before returning memory content or
   identifying metadata, including a cross-persona denial fixture.
3. Prove valid empty canonical memory differs from not configured, timeout,
   unreachable, unauthorized, and malformed downstream results.
4. Prove runtime, canonical memory, and materialization failures do not erase
   or rewrite one another's evidence.
5. Prove auth-ready with missing, stale, or failed smoke is not usable, and
   incomplete persona dependencies cannot produce a definitive ready result.
6. Prove quota/usage provenance and coverage; absent or stale numeric evidence
   remains unknown rather than receiving a reassuring substitute.
7. Prove code entry is advertised only by the active opaque reauth state and
   that post-reauth success remains verifying pending a fresh probe.

Each proof should name the exact test command and immutable commit/PR or
reviewed artifact ref. Branch names, proposed JSON, mounts, and workspace files
are not sufficient evidence.

## 4. Minimal Fixture and Operator Journey Packet

The implemented DTO revision must publish sanitized fixtures for:

- valid runtime plus available-empty canonical memory;
- valid runtime plus timed-out or unreachable Memory Plane;
- canonical memory available plus failed materialization;
- cross-persona private-memory denial without content or identity leakage;
- auth ready plus missing, stale, and failed live-smoke variants;
- unavailable auth plus incomplete persona dependencies;
- known quota with failed smoke and unknown/stale quota without substitution;
- reauth awaiting code, credential-flow success, and fresh probe pending; and
- mixed Codex, Claude, and OpenClaw degradation without pool-wide flattening.

The operator journey must preserve usable evidence while one child degrades:

1. Render runtime, canonical memory, materialization, provider evidence, and
   quota provenance as independently inspectable sections.
2. Display source and observation age for claims that can become stale.
3. Enable only actions advertised by the BFF. Memory retry, materialization
   recovery, reauth, code entry, and normal invoke are separate decisions.
4. Preserve the prior bounded provider failure while reauth is verifying; a
   fresh passing probe may supersede it.

## 5. Frontend Dispatch Capsule

Once section 3 is closed and reviewed, the parent may send this instruction:

```text
Implement in ajoe734/execute-plans against Pantheon BFF revision <ref> and
fixture revision <ref>. Render the BFF-owned query answers independently,
including source, freshness, completeness, bounded reason, and advertised
actions. Use BFF-computed provider usability. Cover every accepted fixture in
component/E2E tests and validate strict live-BFF mode before hosted smoke.
```

Frontend code belongs only in `ajoe734/execute-plans`. Browser requests must
not target Memory Plane, the OpenClaw adapter, or provider APIs, and frontend
logic must not infer readiness from mounts, auth alone, quota, or selected
model refs.

## 6. Parent Absorption Record

Parent owner `Claude2` should record `absorb`, `absorb-with-conditions`, or
`defer`, plus accepted dependency refs, BFF/fixture refs, verification commands,
frontend task/PR, and residual conditions. Any unresolved authority,
authorization, identity, freshness, completeness, action, or fixture-fidelity
condition keeps dispatch deferred.

This packet does not claim that dependencies are accepted, canonical Memory
Plane reads are implemented, an aggregate DTO or reviewed fixture revision
exists, or frontend work is ready. Reviewer `Antigravity` reviews only this
support artifact's accuracy, boundary discipline, and usefulness. Approval
does not make it canonical or require parent absorption.
