# OCLAW-PMEM-004 BFF Handoff Follow-up 17

**Sidecar Task ID**: `OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-17`  
**Parent Task**: `OCLAW-PMEM-004`  
**Parent Owner**: `Claude2`  
**Sidecar Owner**: `Codex`  
**Sidecar Reviewer**: `Antigravity`  
**Helper Kind**: `bff_handoff_packet`  
**Generated**: 2026-07-11  
**Mutates Canonical**: `no`

This support-only packet gives the parent a fillable evidence manifest for the
BFF-to-frontend handoff. It does not accept dependencies, define canonical DTO
names, change BFF/runtime/frontend code, or authorize frontend dispatch.

## 1. Current Gate

**Frontend dispatch remains `defer`.** Durable state records parent
`OCLAW-PMEM-004` as `todo` and dependent on `OCLAW-PMEM-002` and
`OCLAW-PMEM-003`. The parent may change this gate only when one composed BFF
revision and one matching fixture revision prove every required row below.
Route presence, proposed payloads, branch names, mounts, and workspace files
are not composition evidence.

## 2. Parent Evidence Manifest

The parent should copy and complete one row per assertion. Every row must bind
to the same composed BFF commit unless an explicit compatibility boundary is
recorded and tested.

| Assertion | Dependency ref | BFF ref | Fixture ref | Exact passing command | Result |
|---|---|---|---|---|---|
| `MEM-EMPTY`: authorized zero-entry read is available-empty; denial/failure is not empty | `<OCLAW-PMEM-003 ref>` | `<required>` | `<required>` | `<required>` | `<pass/fail>` |
| `MEM-ISOLATE`: cross-persona denial leaks no content, IDs, counts, or identifying metadata | `<OCLAW-PMEM-003 ref>` | `<required>` | `<required>` | `<required>` | `<pass/fail>` |
| `MAT-LINEAGE`: attempt/result and source-memory generation prove materialization | `<OCLAW-PMEM-003 ref>` | `<required>` | `<required>` | `<required>` | `<pass/fail>` |
| `RUNTIME-JOIN`: profile identity/generation is explicit and mismatch fails closed | `<OCLAW-PMEM-002 ref>` | `<required>` | `<required>` | `<required>` | `<pass/fail>` |
| `PROVIDER-USABLE`: auth plus fresh passing smoke plus complete dependencies is required | `<authority ref>` | `<required>` | `<required>` | `<required>` | `<pass/fail>` |
| `QUOTA-PROVENANCE`: source, coverage, window, observation time, freshness, and unknown remain distinct | `<authority ref>` | `<required>` | `<required>` | `<required>` | `<pass/fail>` |
| `REAUTH-STATE`: role/MFA, opaque session, advertised actions, and post-success verification hold | `<authority ref>` | `<required>` | `<required>` | `<required>` | `<pass/fail>` |
| `CHILD-ISOLATE`: failure of one child neither erases nor validates another | `<dependency refs>` | `<required>` | `<required>` | `<required>` | `<pass/fail>` |

A row is incomplete if it cites only a task ID, mutable branch, unreviewed
workspace diff, or prose claim. The exact command must exercise the pinned
revision and record a passing result.

## 3. Evidence Record Requirements

For each assertion, retain enough information for the parent reviewer and the
later frontend implementer to reproduce the observable state:

- route, request identity, expected HTTP status, and response envelope;
- profile, memory, materialization, provider, and session identities or
  generations involved in the join;
- source observation times, freshness inputs, and completeness indicators;
- bounded status/reason values and BFF-advertised actions;
- sanitized fixture identity and its pinned BFF/schema revision; and
- the exact focused command and result.

A join mismatch, stale observation, partial dependency inventory, unknown
quota, or absent source must remain visible. A top-level summary must not turn
any such child into ready.

## 4. Operator Journey Proof

The revision-locked fixtures must let a frontend prove this journey using BFF
responses alone:

1. Persona detail identifies runtime route and profile generation even when a
   memory child is unavailable.
2. Canonical memory distinguishes available-with-items, available-empty,
   unauthorized, malformed, timed out, and unavailable.
3. Materialization retains independent attempt/result lineage; cache or
   workspace presence never proves success.
4. Provider rows retain independent auth, live-smoke, dependency, quota, and
   reauth evidence, with usability computed by the BFF.
5. The UI exposes only actions advertised by the BFF. Code entry appears only
   for the active opaque session when advertised.
6. Credential success enters `verifying`; only a later fresh probe may change
   BFF-computed usability.

The minimum scenario set must include mixed Codex, Claude, and OpenClaw
degradation without pool-wide flattening; stale or mismatched generations;
known quota beside failed smoke; unknown/stale quota without numeric
substitution; incomplete dependencies; and reauth awaiting-code, verifying,
probe-success, probe-failure, and expiry states.

## 5. Dispatch Decision Rule

The parent may record `ready` only when:

- all manifest rows pass against immutable, reviewed references;
- fixtures match the implemented schema, reasons, and advertised actions;
- no unresolved issue affects authority, authorization, identity, freshness,
  completeness, field meaning, action safety, or fixture fidelity; and
- an `ajoe734/execute-plans` task is pinned to the accepted BFF and fixture
  revisions.

Otherwise the decision remains `defer`. `Absorb-with-conditions` is a parent
composition outcome, not permission to dispatch when a condition affects any
of the gates above.

## 6. Frontend Handoff Capsule

Only after section 5 passes may the parent issue:

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
never beneath Pantheon. Browser requests must not target Memory Plane, the
OpenClaw adapter, or provider APIs directly.

## 7. Parent Absorption Record

```text
Decision: absorb | absorb-with-conditions | defer
Accepted OCLAW-PMEM-002 ref: <immutable ref>
Accepted OCLAW-PMEM-003 ref: <immutable ref>
Composed BFF ref: <immutable ref>
Fixture manifest ref: <immutable ref>
Assertions proven: <list>
Focused verification: <exact commands and results>
execute-plans task/PR: <pinned ref or not-dispatched>
Failed or incomplete rows: <none or list>
Residual conditions: <none or bounded list retaining defer>
```

Parent owner `Claude2` owns absorption, canonical field names, implementation,
and frontend dispatch. Reviewer `Antigravity` reviews only this artifact's
accuracy, support-only boundary, and handoff usefulness. Sidecar approval does
not approve the parent, dependencies, frontend readiness, or deployment.
