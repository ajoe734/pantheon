# OCLAW-PMEM-005 BFF Handoff Follow-up 5

- **Sidecar Task ID**: `OCLAW-PMEM-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-5`
- **Parent Task**: `OCLAW-PMEM-005`
- **Parent Owner**: `Codex`
- **Sidecar Owner**: `Codex2`
- **Sidecar Reviewer**: `Antigravity`
- **Helper Kind**: `bff_handoff_packet`
- **Generated**: 2026-07-11
- **Mutates Canonical**: `no`

This is a support-only integration handoff. It does not implement or define a
canonical BFF DTO, Memory Plane contract, OpenClaw materializer, provider
policy, frontend, registry, dev gate, or governance behavior. The parent owner
decides whether to absorb it.

## 1. Verified Query Gap

The current persona-memory reads in `services/control-plane/bff/main.py` obtain
`read_store.list_memory_updates_for_persona` through `getattr`. An absent
reader can therefore produce the same ordinary empty success shape as a
completed authorized query with zero canonical entries. That shape is not
proof that the Memory Plane was queried.

The parent gate must keep `available_empty` distinct from source missing,
timeout, authorization failure, invalid upstream data, and query failure. The
existing runtime-profile and provider readiness/auth projections are useful
inputs, but neither is observed OpenClaw convergence or a live provider call.

## 2. BFF Ownership Decision Handoff

The parent may add a gate-specific projection or compose existing routes on
the server. This packet deliberately leaves that implementation choice open,
but the ownership boundary should remain:

| Concern | Server-owned decision | Frontend responsibility |
|---|---|---|
| Run correlation | Issue one opaque run ID and bind deployment, persona, provider, retrieval, materialization, isolation, and freshness observations to it. | Display the run identity; never join evidence from separate requests into a pass. |
| Canonical retrieval | Record whether an authorized Memory Plane query completed and preserve safe stable IDs. | Distinguish valid empty from unavailable/failed. |
| Runtime convergence | Compare desired profile with observed OpenClaw persona, model route, workspace, and generation. | Render a safe drift diff and remediation owner. |
| Provider usability | Evaluate the required provider/model live smoke separately from auth/readiness and fallback. | Show both checks; do not convert fallback success into primary success. |
| Materialization | Compare canonical source IDs with derived workspace readback from the same generation. | Label workspace evidence as derived cache. |
| Isolation | Execute the cross-persona negative probe and suppress foreign private payloads. | Render only verdict and safe fixture identifiers. |
| Final verdict | Apply freshness and completeness rules and emit pass/fail with reasons. | Present the server verdict; never recompute it. |

The frontend remains in `ajoe734/execute-plans` and calls Pantheon BFF routes
only. Hosted dev proof must use live BFF mode, the Pantheon-owned BFF origin,
and strict fallback. No browser call should reach Memory Plane, provider APIs,
the OpenClaw adapter, or VM workspace files directly.

## 3. Reason Semantics for Handoff

These labels are semantic examples for implementation and UI mapping, not a
new canonical enum. The parent should translate them into existing repository
conventions.

| Meaning | Blocking | Retry scope | Safe operator detail |
|---|---:|---|---|
| Canonical query completed with zero authorized items | No | None | Source and observation time |
| Memory reader/source is not configured | Yes | Full run after configuration repair | Owning service only |
| Memory query timed out or returned invalid data | Yes | New full run | Sanitized class and timestamp |
| Requested persona is not authorized for private memory | Yes | No blind retry | Persona ID and policy outcome, no content |
| Required live provider smoke is missing, stale, or failed | Yes | New full run | Provider/model and sanitized probe ID |
| Desired and observed runtime identity differ | Yes | New run after convergence | Safe persona/model/workspace identifiers |
| Workspace source IDs or generation do not match | Yes | New run after rematerialization | Safe ID sets and generations |
| Foreign private ID/content appears at either boundary | Critical | Stop publication; investigate | Fixture ID and boundary, never payload |
| Every observation is fresh and correlated | No | None | Evidence links and deployed revisions |

A retry that can change provider, runtime, retrieval, or materialization
correlation must create a new verification run (or atomically replace the
whole server snapshot). Retrying one UI card must not preserve unrelated green
observations from an earlier generation.

## 4. Parent Acceptance Matrix

The parent implementation and evidence should cover:

- authorized canonical retrieval with zero items as a valid empty state;
- missing reader, timeout, malformed response, and authorization failure as
  explicit non-success observations;
- auth-ready plus failed or absent required live smoke as failure;
- fallback success while a required primary path remains failed;
- desired runtime profile without matching observed OpenClaw identity;
- workspace presence without canonical source-ID readback;
- matching IDs from different generations as stale/mixed evidence;
- a foreign private ID at BFF or workspace boundary as a critical failure
  without payload disclosure; and
- one fresh run where provider, observed runtime, canonical retrieval,
  materialization, and isolation all correlate to a server-owned pass.

Component tests may prove envelope, reason, and correlation behavior. They do
not replace hosted live invocation, observed runtime identity, workspace
readback, or the negative cross-persona probe.

## 5. Composition and Closeout

Before closing `OCLAW-PMEM-005`, `Codex` should compose reviewer-accepted
outputs from `OCLAW-PMEM-002`, `003`, and `004`, then retain child PR and merge
SHAs, deployed BFF/frontend revisions, exact commands and timestamps,
sanitized positive and negative snapshots, the freshness policy, and residual
risks. `Antigravity` reviews this packet only for factual accuracy,
support-only scope, fail-closed query semantics, server-owned correlation,
Memory Plane authority, derived-cache labeling, and mandatory live smoke and
isolation evidence.

## 6. Non-Claims

This packet does not claim the current persona-memory response is canonical,
readiness proves provider usability, desired runtime configuration proves
convergence, workspace existence proves materialization, component tests are
hosted evidence, or any described BFF/frontend behavior has been implemented
or deployed. Reviewer approval only makes this support material available to
the parent owner.
