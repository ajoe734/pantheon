# OCLAW-PMEM-004 BFF Handoff Follow-up 16

**Sidecar Task ID**: `OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-16`  
**Parent Task**: `OCLAW-PMEM-004`  
**Parent Owner**: `Claude2`  
**Sidecar Owner**: `Codex`  
**Sidecar Reviewer**: `Antigravity`  
**Helper Kind**: `bff_handoff_packet`  
**Generated**: 2026-07-11  
**Mutates Canonical**: `no`

This support-only packet gives the parent a revision-locked, testable handoff
contract. It does not accept dependencies, define canonical DTO names, change
BFF or frontend code, or authorize frontend dispatch.

## 1. Dispatch Decision

**Decision: `defer`.** Durable task state still records parent
`OCLAW-PMEM-004` as `todo`, dependent on `OCLAW-PMEM-002` and
`OCLAW-PMEM-003`. The parent may replace `defer` only after accepted dependency
refs, one composed BFF ref, and a matching fixture ref satisfy every assertion
below. Route presence or proposed JSON is not implementation evidence.

## 2. Executable Handoff Contract

The parent should bind each assertion to an immutable BFF/fixture revision and
an exact passing command. Names below identify behavior, not canonical fields.

| Assertion ID | Operator-visible invariant | Required negative proof |
|---|---|---|
| `MEM-EMPTY` | An authorized canonical read with zero entries is visibly available and empty. | Timeout, denial, malformed response, and unavailable source never become empty. |
| `MEM-ISOLATE` | A persona can receive only its authorized canonical memory projection. | Cross-persona request leaks no content, IDs, counts, or identifying metadata. |
| `MAT-LINEAGE` | Materialization identifies its attempt/result and source-memory generation. | Workspace/cache existence never proves success or becomes memory authority. |
| `RUNTIME-JOIN` | Runtime profile identity and generation remain visible beside memory evidence. | Mismatched or stale generations fail closed instead of silently joining. |
| `PROVIDER-USABLE` | BFF-computed usability requires auth, a fresh passing smoke, and complete required dependencies. | Mounted/auth-ready, known quota, or model selection alone never yields usable. |
| `QUOTA-PROVENANCE` | Quota/usage retains source, coverage, window, observation time, and freshness. | Unknown, stale, unsupported, or partial data never becomes zero, unlimited, or healthy. |
| `REAUTH-STATE` | Role/MFA-gated reauth exposes an opaque session and only server-advertised actions. | Credential success remains verifying until a later fresh probe; secrets are never projected. |
| `CHILD-ISOLATE` | Runtime, memory, materialization, auth, smoke, dependencies, quota, and reauth remain independently observable. | Failure or absence of one child neither erases nor falsely validates another. |

For every assertion, the evidence record must include:

- composed BFF commit and schema/fixture revision;
- route and expected HTTP status/envelope;
- input identity/generation and observation/freshness values;
- expected bounded reason and advertised actions;
- exact executable test command and passing result.

## 3. Operator Journey Checkpoints

The frontend handoff is coherent only when the accepted fixtures prove this
sequence without browser-side joins:

1. Persona detail identifies the runtime profile and generation.
2. Canonical memory reports available-empty, available-with-items,
   unauthorized, or unavailable distinctly.
3. Materialization reports its own lineage and failure independently of the
   canonical read.
4. Provider rows render auth, smoke, dependencies, quota, and usability as
   separate evidence.
5. The UI enables only BFF-advertised probe, refresh, or reauth actions.
6. Code entry appears only for the active opaque session when advertised.
7. Reauth credential success renders verifying; a later fresh BFF probe alone
   can change BFF-computed usability.

The browser must call Pantheon BFF routes only. It must not query Memory Plane,
the OpenClaw adapter, or providers directly, and must not derive readiness.

## 4. Minimum Revision-Locked Fixtures

The parent-owned manifest should cover:

- authorized memory with items and authorized available-empty memory;
- unauthorized, timeout, unreachable, and malformed memory responses;
- canonical memory available while materialization fails;
- cross-persona denial without metadata leakage;
- stale or mismatched runtime/memory/materialization generations;
- auth-ready with missing, stale, failed, and passing smoke evidence;
- incomplete dependency inventory;
- known quota beside failed smoke, plus unknown and stale quota;
- reauth awaiting code, credential success, verifying, probe success, probe
  failure, and expiry;
- mixed Codex, Claude, and OpenClaw states without pool-wide flattening.

Fixtures are invalid as handoff evidence if they invent fields, reasons, or
actions absent from the pinned BFF revision.

## 5. Frontend Dispatch Capsule

Only after section 2 is complete and reviewed may the parent issue:

```text
Implement in ajoe734/execute-plans against Pantheon BFF commit <immutable-ref>
and fixture revision <immutable-ref>. Call Pantheon BFF routes only. Render
runtime, canonical memory, materialization, provider auth, live smoke,
dependency completeness, quota/usage, and reauth independently. Preserve
identity/generation, source, bounded reason, observation time, freshness,
completeness, and server-advertised actions. Use BFF-computed usability. Cover
every pinned fixture in component/E2E tests and validate strict live-BFF mode
before hosted smoke.
```

Frontend source and build configuration belong in `ajoe734/execute-plans`,
never under the Pantheon checkout.

## 6. Parent Absorption Record

```text
Decision: absorb | absorb-with-conditions | defer
Accepted OCLAW-PMEM-002 ref: <immutable ref>
Accepted OCLAW-PMEM-003 ref: <immutable ref>
Composed BFF ref: <immutable ref>
Fixture manifest ref: <immutable ref>
Assertion IDs proven: <list>
Focused verification: <exact commands and results>
execute-plans task/PR: <pinned ref or not-dispatched>
Failed assertions: <none or list>
Residual conditions: <none or bounded list retaining defer>
```

Parent owner `Claude2` owns absorption, canonical field names, implementation,
and frontend dispatch. Reviewer `Antigravity` reviews only this artifact's
accuracy, support-only boundary, and handoff usefulness. Sidecar approval does
not approve the parent, dependencies, frontend readiness, or deployment.
