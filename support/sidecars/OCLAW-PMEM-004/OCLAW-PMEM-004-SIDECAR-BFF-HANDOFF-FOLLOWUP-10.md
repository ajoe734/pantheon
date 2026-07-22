# OCLAW-PMEM-004 BFF Handoff Follow-up 10

**Sidecar Task ID**: `OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-10`
**Parent Task**: `OCLAW-PMEM-004`
**Parent Owner**: `Claude2`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Antigravity`
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-07-11
**Mutates Canonical**: `no`

This support-only packet converts the prior contract-freeze checklist into an
implementation evidence ledger and operator-query handoff. It does not define
canonical field names, approve a dependency, modify BFF/runtime behavior, or
authorize frontend implementation.

## 1. Dispatch Decision

**Frontend dispatch remains `defer`.** Route presence and isolated route tests
are not evidence that the parent aggregate contract, degradation vocabulary,
fixture set, and accepted dependency revisions compose. The parent owner may
dispatch only after the evidence ledger below is complete and reviewed.

## 2. Evidence Ledger for Parent Absorption

The parent should replace every placeholder with an immutable commit/PR or a
reviewed artifact plus an exact focused verification command.

| Boundary | Current repository contact point | Missing composition evidence | Accepted ref / verification |
|---|---|---|---|
| Runtime routing | `GET /bff/personas/{persona_id}/runtime-profile` in `services/control-plane/bff/main.py` | Accepted `OCLAW-PMEM-002` authority and tests for invalid/unknown model refs and degraded routing | `<required>` |
| Canonical memory | `GET /bff/personas/{persona_id}/memory` in `services/control-plane/bff/main.py` | Accepted `OCLAW-PMEM-003` facade; authorized scope; available-empty versus unavailable; canonical IDs | `<required>` |
| Materialization | Persona memory/operator projection to be composed by parent | Independent attempt/result identity, lineage, and failure semantics that never erase canonical entries | `<required>` |
| Provider inventory | `GET /bff/assistant/providers`; adapter delegation supports `auth_probe` | Aggregate tests separating auth observation, live-smoke freshness, dependency completeness, quota provenance, and BFF-computed usability | `<required>` |
| Usage/quota | `GET /bff/assistant/providers/usage-summary` | Unknown/stale/error provenance and tests proving unknown never becomes zero, unlimited, or healthy | `<required>` |
| Reauth | `POST /bff/assistant/provider/reauth`, status GET, and code POST; operator/MFA and redaction tests exist | Parent aggregate action vocabulary and post-success `verifying` transition tied to a subsequent fresh probe | `<required>` |
| Fixtures | No reviewed revision recorded by this sidecar | Sanitized cross-surface fixtures pinned to the implemented DTO and bounded reasons | `<required>` |
| Frontend | Must be implemented in `ajoe734/execute-plans` | Task/PR pinned to accepted BFF and fixture revisions; strict-live component/E2E evidence | `<required>` |

No row may be closed by a branch name, workspace/mount presence, proposed JSON,
or an unreviewed diff.

## 3. BFF Query Gap and Join Rules

The parent aggregate must answer these operator questions without making the
browser infer health:

| Operator question | Server-owned evidence | Required independence |
|---|---|---|
| Where will this persona run? | Runtime route mode, selected/fallback refs, generation/source refs, validation result | Render even when memory is unavailable. |
| Can canonical memory be read now? | Authorized retrieval status, bounded reason, retrieval time, canonical IDs/scope | Available with zero entries is not unavailable. |
| Was memory materialized into OpenClaw? | Attempt/result identity, generation, source-entry lineage, observed time | Failure does not hide canonical memory. |
| Can this provider serve work now? | Auth observation plus fresh passing live smoke and complete required dependencies | Auth-ready alone is not usable. |
| Is capacity known? | Quota/usage value, provenance, observation time, freshness | Unknown is not zero, unlimited, or healthy. |
| What may the operator do next? | Bounded BFF-advertised actions with role/MFA requirements | Browser does not invent retry or reauth actions. |
| Did reauth restore service? | Opaque session state followed by a new readiness/live-smoke observation | Reauth success is `verifying`, not immediately usable. |

The join must preserve per-source `status`, `reason`, `observed_at`, and
freshness/completeness evidence. A top-level summary may exist, but it must not
flatten an unknown, stale, partial, unavailable, or failed child into ready.

## 4. Operator Journey and Allowed Actions

1. Load runtime, canonical memory/materialization, and provider evidence in
   independently renderable sections.
2. Show source and observation age beside every claim that can become stale.
3. If runtime is valid but memory is unavailable, retain routing and offer
   only a BFF-advertised memory retry.
4. If canonical memory is available but materialization failed, retain entries
   and show a separate cache/materialization recovery action.
5. If auth is ready but smoke is stale/missing/failed, label the provider
   unknown or degraded and do not enable normal invoke as usable.
6. Start reauth only through the BFF role/MFA-gated route. Display code entry
   only when the active opaque session advertises it; never expose tokens.
7. After reauth reports success, show verifying until a fresh probe establishes
   usability. Preserve the prior failure reason until superseded by evidence.

## 5. Minimum Cross-Surface Fixtures

Each fixture must name its DTO revision, bounded reasons, observation times,
allowed actions, and expected copy:

- valid runtime plus available-empty canonical memory;
- valid runtime plus unreachable/timed-out Memory Plane;
- canonical memory available plus failed materialization;
- auth ready plus missing or stale live smoke;
- failed smoke plus known quota;
- unavailable auth plus partial dependency inventory;
- unknown/stale quota without numeric substitution;
- reauth awaiting code, then success with fresh probe pending;
- mixed Codex, Claude, and OpenClaw degradation without pool-wide flattening;
- cross-persona private-memory denial without content or identifying leakage.

## 6. Frontend Handoff Capsule

After the ledger is complete, the parent may hand off this bounded instruction:

```text
Implement in ajoe734/execute-plans against Pantheon BFF revision <ref> and
fixture revision <ref>. Use BFF routes only. Render BFF-computed usability and
preserve independent runtime, canonical-memory, materialization, auth,
live-smoke/freshness, dependency-completeness, quota-provenance, and reauth
states. Enable only BFF-advertised actions. Validate strict live BFF mode with
the accepted degradation fixtures before hosted smoke.
```

Frontend source must not be materialized in Pantheon. The browser must not call
Memory Plane, OpenClaw adapter, or provider APIs directly, and must not derive
usability from credential mounts, auth alone, quota, or model selection.

## 7. Parent Absorption Record

The parent owner should record one outcome: `absorb`,
`absorb-with-conditions`, or `defer`, together with dependency, BFF, fixture,
and frontend refs and any residual conditions. A condition affecting authority,
authorization, freshness, completeness, field meaning, allowed actions, or
fixture fidelity blocks a ready dispatch.

This packet does not claim `OCLAW-PMEM-002` or `OCLAW-PMEM-003` is accepted,
that persona memory currently reads canonical Memory Plane, that an aggregate
DTO or fixture revision exists, or that frontend delivery is ready. `Claude2`
owns absorption. `Antigravity` reviews only this support artifact's accuracy,
scope, and usefulness; approval does not promote it to canonical truth.
