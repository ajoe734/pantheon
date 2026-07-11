# OCLAW-PMEM-004 BFF Handoff Follow-up 12

**Sidecar Task ID**: `OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-12`  
**Parent Task**: `OCLAW-PMEM-004`  
**Parent Owner**: `Claude2`  
**Sidecar Owner**: `Codex2`  
**Sidecar Reviewer**: `Antigravity`  
**Helper Kind**: `bff_handoff_packet`  
**Generated**: 2026-07-11  
**Mutates Canonical**: `no`

This support-only worksheet gives the parent owner a concrete way to record
composition evidence and produce a bounded frontend handoff. It does not
define canonical DTOs, approve dependency work, modify BFF/frontend code, or
authorize dispatch by itself.

## 1. Dispatch Gate

**Current recommendation: `defer`.** Durable state still records the parent as
`todo`, dependent on `OCLAW-PMEM-002` and `OCLAW-PMEM-003`. Existing routes
are useful seams, but route presence does not prove that identity, authority,
authorization, degradation semantics, or cross-surface fixtures compose.

The parent may change the decision to `ready` only when every required cell in
sections 2 and 3 contains an immutable accepted reference and focused test
evidence. `ready-with-conditions` is not suitable when a condition affects
authority, authorization, join identity, field meaning, freshness,
completeness, allowed actions, or fixture fidelity.

## 2. BFF Composition Acceptance Worksheet

The parent should complete this table in its task artifact. Branch names,
workspace or mount presence, proposed JSON, and unreviewed diffs do not close a
row.

| Operator answer owned by BFF | Required upstream identity/authority | Required result semantics | Accepted ref and focused verification |
|---|---|---|---|
| Persona runtime route | Accepted runtime-profile revision; persona ID; profile/sync generation; model-ref validation authority | Route, primary/fallback relations, validation status, source and observed time remain visible when other surfaces fail | `<required>` |
| Canonical persona memory | Accepted Memory Plane revision; authorized persona/scope identity; canonical memory IDs | `available-empty`, `unavailable`, `unauthorized`, timeout/unreachable, and malformed downstream are distinguishable without content leakage | `<required>` |
| OpenClaw materialization | Canonical source memory IDs; materialization attempt/result ID; target generation or cache ref | Materialization failure is independent and never hides or rewrites canonical entries | `<required>` |
| Provider usability | Provider identity; auth observation; live-smoke observation; dependency inventory revision | BFF computes usability; auth-ready without a fresh passing smoke, or with incomplete required dependencies, is not usable | `<required>` |
| Provider capacity | Provider identity; quota/usage source and coverage window | Known, unknown, stale, unsupported, and source-error remain distinct; unknown is never zero, unlimited, or healthy | `<required>` |
| Recovery actions | Provider/session identity; operator role/MFA result; current opaque reauth state | Only server-advertised actions are enabled; code entry is session-bound; secrets are redacted | `<required>` |
| Post-reauth verification | Reauth terminal result joined to a later readiness/smoke observation | Credential-flow success becomes `verifying`; only a fresh passing probe can supersede the prior usability reason | `<required>` |

Every child projection must preserve its own bounded `status`, `reason`,
`source`, observation time, freshness, and completeness. Exact field names are
parent-owned. A top-level summary must not flatten an unknown, stale, partial,
unavailable, unauthorized, or failed child into ready.

## 3. Executable Evidence Manifest

Before frontend dispatch, the parent should publish a sanitized manifest with
one row per fixture and pin it to the implemented BFF revision:

| Fixture ID | Required setup | Required operator result | Required allowed actions | Test / immutable ref |
|---|---|---|---|---|
| `memory-available-empty` | Valid runtime; authorized canonical read returns zero entries | Genuine empty state with source/observation evidence | Only actions advertised by BFF | `<required>` |
| `memory-unreachable` | Valid runtime; Memory Plane timeout/unreachable | Runtime remains visible; memory unavailable with bounded reason | Retry only if advertised | `<required>` |
| `materialization-failed` | Canonical entries available; OpenClaw materialization fails | Entries remain visible; failure and source lineage are separate | Materialization recovery only if advertised | `<required>` |
| `memory-cross-persona-denied` | Requester lacks access to another persona's private memory | Denial contains no memory content or identifying metadata | No browser-invented bypass/retry | `<required>` |
| `smoke-stale-or-missing` | Auth ready; no fresh passing live smoke | Provider is unknown/degraded, never usable | Probe/refresh only if advertised | `<required>` |
| `smoke-failed-quota-known` | Auth ready; smoke failed; capacity known | Failure remains decisive; quota does not override usability | Reauth/retry only if advertised | `<required>` |
| `dependencies-incomplete` | Auth observation exists; persona-profile inventory partial | Dependency count is explicitly incomplete; provider is not promoted to ready | Refresh only if advertised | `<required>` |
| `quota-unknown-or-stale` | Quota source absent, unsupported, errored, or stale | No numeric substitute and no reassuring capacity claim | None unless advertised | `<required>` |
| `reauth-awaiting-code` | Active opaque session requests code | Code form is shown only for that state/session | Submit code/cancel as advertised | `<required>` |
| `reauth-success-probe-pending` | Credential flow succeeds; new probe not complete | Provider remains verifying; prior reason stays inspectable | Refresh/probe as advertised | `<required>` |
| `mixed-provider-degradation` | Codex, Claude, and OpenClaw have different evidence states | Each row retains its own reason/freshness; no pool-wide flattening | Per-row advertised actions only | `<required>` |

Each fixture record should include the BFF commit, schema/fixture revision,
bounded reason vocabulary used, observation timestamps/freshness inputs, exact
test command, and expected HTTP status/envelope. This makes the frontend task
consume executable evidence rather than prose alone.

## 4. Operator Journey Acceptance

The composed journey is acceptable only when these transitions are proven:

1. Persona runtime, canonical memory, materialization, provider health, and
   capacity render independently, so one unavailable child does not erase
   usable evidence from another.
2. Available-empty memory has different copy from unavailable, unauthorized,
   timed-out, unreachable, or malformed memory responses.
3. Canonical entries remain visible while materialization is failed or being
   retried, and workspace/cache presence is labelled derived evidence only.
4. Auth-ready plus missing, stale, or failed smoke never enables normal invoke
   as usable; incomplete dependencies also prevent a definitive ready claim.
5. Reauth start/status/code uses only BFF routes with role/MFA enforcement and
   opaque session state. Code entry appears only when advertised.
6. Reauth success transitions to verifying. A subsequent fresh probe, not the
   credential-flow result, determines restored usability.

## 5. Frontend Dispatch Record

When sections 2 through 4 are complete, the parent should record:

```text
Decision: ready | defer
Accepted OCLAW-PMEM-002 ref: <immutable ref>
Accepted OCLAW-PMEM-003 ref: <immutable ref>
BFF implementation ref: <immutable ref>
Fixture manifest ref: <immutable ref>
Focused verification: <exact commands>
execute-plans task/PR: <pinned task or PR>
Residual conditions: <none, or list that keeps decision deferred>
```

The bounded frontend instruction is:

```text
Implement in ajoe734/execute-plans against BFF revision <ref> and fixture
revision <ref>. Use Pantheon BFF routes only. Render the BFF-owned answers and
their source, reason, freshness, completeness, and advertised actions without
deriving usability in the browser. Cover every accepted fixture in component
or E2E tests and validate strict live-BFF mode before hosted smoke.
```

Frontend source belongs only in `ajoe734/execute-plans`. Browser requests must
not target Memory Plane, the OpenClaw adapter, or provider APIs directly.

## 6. Absorption and Non-Claims

Parent owner `Claude2` decides `absorb`, `absorb-with-conditions`, or `defer`
and owns all final field names, join implementation, tests, and frontend
dispatch. Reviewer `Antigravity` reviews only this sidecar's boundary accuracy
and usefulness.

This packet does not claim that `OCLAW-PMEM-002` or `OCLAW-PMEM-003` is
accepted, that the current persona-memory route reads canonical Memory Plane,
that provider readiness or quota composition is implemented, that fixtures
exist, or that frontend work is ready or deployed. Approval of this support
artifact does not promote it into canonical contract truth.

## 7. Closeout

Reviewer `Antigravity` approved this support-only worksheet on 2026-07-11
without requested changes. The approval covers the bounded `defer` gate,
composition worksheet, fixture manifest, operator journey, and frontend
handoff boundary; it does not approve the parent implementation or promote
this packet into canonical truth.

Owner finalization re-read the approval and task state, confirmed that the
artifact remains limited to the declared sidecar scope, and ran:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh show \
  OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-12
git diff --check
```

The dispatch recommendation remains `defer`. Parent owner `Claude2` retains
the decision to absorb this worksheet and must supply the immutable dependency,
BFF, fixture, and focused-verification references before frontend dispatch.
This sidecar is ready to merge into `dev` and close after that merge.
