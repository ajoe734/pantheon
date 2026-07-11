# OCLAW-PMEM-004 BFF Handoff Follow-up 15

**Sidecar Task ID**: `OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-15`
**Parent Task**: `OCLAW-PMEM-004`
**Parent Owner**: `Claude2`
**Sidecar Owner**: `Codex2`
**Sidecar Reviewer**: `Antigravity`
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-07-11
**Mutates Canonical**: `no`

This support-only packet turns the existing handoff guidance into a single
parent-owned go/no-go record. It does not accept dependencies, name canonical
DTO fields, change BFF/runtime/frontend code, or authorize frontend dispatch.

## 1. Current Decision

**Frontend dispatch remains `defer`.** The parent may move to `ready` only
when one composed BFF revision and one revision-matched fixture manifest prove
all rows below. Route presence, proposed payloads, branch names, credential
mounts, workspace files, and isolated happy-path tests are not composition
evidence.

## 2. Parent Go/No-Go Record

The parent should copy this table into its implementation or review artifact
and replace every placeholder with an immutable accepted reference plus the
exact passing command. All rows must describe the same composed revision.

| Gate | Required proof | Accepted ref / command | No-go condition |
|---|---|---|---|
| Runtime authority | Accepted `OCLAW-PMEM-002` identity/generation and invalid-route behavior | `<required>` | Unknown or mismatched routing is inferred from workspace/config |
| Memory authority | Accepted `OCLAW-PMEM-003` authorization, canonical IDs/scope, and available-empty behavior | `<required>` | Unavailable, unauthorized, or malformed is flattened to empty |
| Materialization lineage | Attempt/result identity joined to source memory IDs and generation | `<required>` | Cache/workspace presence is treated as success or authority |
| Provider usability | Separately timed auth and fresh live-smoke evidence with a tested freshness rule | `<required>` | Auth-ready, mount presence, quota, or model selection implies usable |
| Dependency completeness | Persona/provider inventory completeness and missing-profile behavior | `<required>` | Partial inventory yields a definitive ready state or zero count |
| Quota provenance | Source, coverage, observation time, freshness, and unknown/error behavior | `<required>` | Unknown or stale becomes zero, unlimited, or healthy |
| Reauth lifecycle | Role/MFA, opaque session, advertised actions, code flow, and post-success probe | `<required>` | Credential success skips `verifying` or exposes secrets |
| Join isolation | Mismatch and child-failure tests preserve independent evidence | `<required>` | One child erases or falsely validates another child |
| Fixture lock | Sanitized manifest pinned to the BFF/schema revision | `<required>` | Fixtures invent fields/actions or drift from implementation |

Any unresolved authority, authorization, identity, freshness, completeness,
field-meaning, action, or fixture-fidelity issue is a hard no-go, not a
`ready-with-conditions` dispatch.

## 3. Operator Query Acceptance

The composed BFF response must let the operator answer these questions without
browser-side joins or health inference:

1. Where will this persona run, and which profile generation proves it?
2. Can canonical memory be read now, including a genuine available-empty
   result distinct from denial or source failure?
3. Was that canonical memory materialized, and which attempt/result and source
   IDs prove it?
4. Can each provider serve work now, based on auth, a fresh passing smoke, and
   complete required dependencies?
5. Is quota known, from which source, for which window, and how fresh is it?
6. Which recovery action is currently permitted by the BFF?
7. After reauth, is service still verifying or proven usable by a later fresh
   probe?

Runtime, canonical memory, materialization, auth, smoke, dependencies, quota,
and reauth must retain independent status, bounded reason, source,
`observed_at`, freshness, and completeness where applicable. A summary must
not flatten an unknown, stale, partial, unavailable, unauthorized, or failed
child into ready.

## 4. Revision-Locked Scenario Set

The fixture manifest and focused tests should cover at least:

- authorized available-empty canonical memory;
- unauthorized, timed-out, unreachable, and malformed Memory Plane results;
- canonical memory available while materialization fails;
- cross-persona denial without content, identifiers, or count leakage;
- auth-ready with missing, stale, failed, and passing live smoke;
- incomplete persona dependency inventory;
- known quota beside failed smoke and unknown/stale quota without substitution;
- reauth awaiting code, credential success, verifying, and later probe result;
- mixed Codex, Claude, and OpenClaw states without pool-wide flattening; and
- stale or mismatched identities/generations across joined sources.

Each scenario must identify the pinned BFF/schema revision, expected HTTP
status/envelope, bounded reasons, observation/freshness inputs, advertised
actions, and exact executable test command.

## 5. Frontend Dispatch Capsule

Only after section 2 is complete and reviewed may the parent issue:

```text
Implement in ajoe734/execute-plans against Pantheon BFF commit <immutable-ref>
and fixture revision <immutable-ref>. Call Pantheon BFF routes only. Render
runtime, canonical memory, materialization, provider auth, live smoke,
dependency completeness, quota/usage, and reauth independently. Preserve
source, bounded reason, observation time, freshness, completeness, and only
server-advertised actions. Use BFF-computed usability. Cover every pinned
fixture in component/E2E tests and validate strict live-BFF mode before hosted
smoke.
```

Frontend source and build configuration belong in `ajoe734/execute-plans`,
never beneath Pantheon. Browser requests must not target Memory Plane, the
OpenClaw adapter, or providers directly.

## 6. Absorption Baton

```text
Decision: absorb | absorb-with-conditions | defer
Accepted OCLAW-PMEM-002 ref: <immutable ref>
Accepted OCLAW-PMEM-003 ref: <immutable ref>
Composed BFF ref: <immutable ref>
Fixture manifest ref: <immutable ref>
Focused verification: <exact commands and results>
execute-plans task/PR: <pinned ref or not-dispatched>
Failed/no-go rows: <none or list>
Residual conditions: <none or bounded list retaining defer>
```

Parent owner `Claude2` owns absorption, canonical field names, implementation,
and dispatch. Reviewer `Antigravity` reviews only this packet's accuracy,
support-only boundary, and handoff usefulness. Approval does not approve the
parent, dependencies, frontend readiness, or deployment.
