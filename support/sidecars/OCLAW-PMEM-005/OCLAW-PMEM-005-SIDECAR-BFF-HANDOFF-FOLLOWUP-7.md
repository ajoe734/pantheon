# OCLAW-PMEM-005 BFF Handoff Follow-up 7

- **Sidecar Task ID**: `OCLAW-PMEM-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-7`
- **Parent Task**: `OCLAW-PMEM-005`
- **Parent Owner**: `Codex`
- **Sidecar Owner**: `Codex`
- **Sidecar Reviewer**: `Antigravity`
- **Helper Kind**: `bff_handoff_packet`
- **Generated**: 2026-07-11
- **Mutates Canonical**: `no`

This is a support-only absorption and dispatch worksheet. It does not approve,
implement, or define Memory Plane, BFF, OpenClaw, materialization, provider,
frontend, registry, gate, or governance behavior. The parent owner decides
whether to absorb it after dependency review.

## 1. Parent Absorption Decision

**Recommended current decision: `defer`.** At this branch tip, the public
persona-memory path still cannot prove that an empty result came from a
completed canonical query. In `services/control-plane/bff/main.py`, both
`bff_get_persona_memory` and `_pm12_memory_items_for_persona` discover
`read_store.list_memory_updates_for_persona` with `getattr`; an absent reader
becomes an ordinary empty collection.

The parent may change this decision to `ready` only after every row below has
an accepted dependency reference, an implementation owner, and focused test
evidence. `Ready with conditions` is not appropriate when a missing condition
affects source authority, persona authorization, observation identity,
freshness, or private-memory isolation.

| Decision input | Accepted dependency / immutable ref | Owner | Focused evidence | State |
|---|---|---|---|---|
| Canonical BFF query distinguishes completed-empty from unavailable/failed | _fill_ | _fill_ | _fill_ | `open` |
| Observed OpenClaw persona/model/workspace is distinct from desired profile | _fill_ | _fill_ | _fill_ | `open` |
| Required provider live smoke is distinct from auth/readiness/fallback | _fill_ | _fill_ | _fill_ | `open` |
| Materialization readback contains canonical source IDs and one generation | _fill_ | _fill_ | _fill_ | `open` |
| Cross-persona private-memory probe covers BFF and workspace boundaries | _fill_ | _fill_ | _fill_ | `open` |
| Server owns run correlation, freshness, reason codes, and final verdict | _fill_ | _fill_ | _fill_ | `open` |

## 2. BFF Query Handoff

This packet does not prescribe a route or DTO. Whatever parent-owned server
projection is selected must preserve these independent observations:

| Observation | Minimum meaning | Must not be inferred from |
|---|---|---|
| Canonical retrieval | Completed-source status, authorized persona, stable memory/source IDs, observation time | Empty array, workspace file, or desired profile |
| Runtime convergence | Requested identity beside observed OpenClaw persona/model/workspace/generation | Desired runtime profile alone |
| Provider usability | Required provider/model live invocation result and time | Credential mount, auth-ready, quota, reauth, or fallback success |
| Materialization | Derived-cache label, workspace identity, generation, canonical source IDs read back | Workspace or file existence |
| Isolation | Subject persona, safe foreign fixture ID, and separate BFF/workspace verdicts | Missing UI rows or redacted payload alone |
| Correlation | One opaque run, exercised revisions, server freshness policy, server verdict/reasons | Browser clock or independently joined cards |

A completed authorized query with zero entries is a valid empty state. Missing
reader/source, timeout, malformed response, authorization failure, and query
failure are explicit non-success states. A retry that can change correlation
starts a new run or atomically replaces the server snapshot.

## 3. Frontend Dispatch Contract

Frontend implementation belongs in `ajoe734/execute-plans`. It calls Pantheon
BFF routes only; hosted dev proof uses live BFF mode, the Pantheon-owned dev BFF
origin, strict fallback, and safe write defaults.

| Operator step | Required presentation | Blocking transition |
|---|---|---|
| Start verification | Run ID, exercised revisions, observation time; no early verdict | No run identity or mixed revisions |
| Compare runtime | Desired and observed safe identities side by side | Observation missing or drifted |
| Verify provider | Auth/readiness and required live smoke as separate states | Smoke missing, stale, failed, or replaced by fallback |
| Read canonical memory | Distinguish `available empty` from unavailable/failed; show safe IDs | Empty response without completed-source proof |
| Verify materialization | Label workspace output derived cache; compare IDs and generation | IDs absent/different or generation mixed |
| Probe isolation | Show boundary verdicts and safe fixture ID only | Foreign private ID/content reaches either boundary |
| Publish result | Render the fresh server-owned verdict and reasons | Browser would need to recompute or join a pass |

The browser must not call Memory Plane, provider APIs, OpenClaw adapter routes,
or VM workspace files directly. It must not expose credentials, provider
payloads, private memory content, or raw VM paths as evidence.

## 4. Fixture and Evidence Dispatch

The parent should assign each case to a concrete BFF/component test and, where
required, a hosted probe. References below remain blank until dependency work
is accepted; blank cells keep the absorption decision at `defer`.

| Case | Expected server/UI result | Test or evidence ref |
|---|---|---|
| Authorized canonical query returns zero entries | Available empty, with source and time | _fill_ |
| Reader absent, timeout, malformed response, or query failure | Explicit unavailable/failed; gate blocked | _fill_ |
| Foreign persona requests private memory | Denied; no private payload disclosed | _fill_ |
| Auth ready but required smoke fails or is missing | Provider check failed | _fill_ |
| Required primary fails while fallback succeeds | Required-primary check remains failed | _fill_ |
| Desired runtime exists without matching observation | Runtime convergence failed | _fill_ |
| Workspace exists without canonical source IDs | Materialization failed | _fill_ |
| IDs match but generations differ | Mixed/stale evidence rejected | _fill_ |
| Foreign private fixture reaches BFF or workspace | Critical isolation failure, safe identifiers only | _fill_ |
| All observations are fresh and correlated | Server-owned pass rendered without recomputation | _fill_ |

Component tests establish envelope and correlation semantics only. Hosted proof
is still required for live provider invocation, observed OpenClaw identity,
workspace readback, and the cross-persona negative probe.

## 5. Parent Composition Record

Before closing `OCLAW-PMEM-005`, retain:

- accepted `OCLAW-PMEM-002`, `003`, and `004` PR numbers and merge SHAs;
- deployed Pantheon/BFF, OpenClaw/adapter, and execute-plans revisions;
- exact commands, timestamps, sanitized positive and negative snapshots;
- canonical-to-materialized source-ID equality from the same run/generation;
- required provider smoke distinct from readiness and fallback;
- both BFF and workspace isolation verdicts; and
- evidence freshness, unexercised required paths, and residual risks.

`Codex` owns parent composition and all executable or canonical changes.
`Antigravity` reviews only this packet's accuracy, support-only boundary,
fail-closed empty/unavailable distinction, server-owned correlation, Memory
Plane authority, derived-cache labeling, and mandatory smoke/isolation proof.
Approval makes this worksheet available to the parent; it does not claim any
described behavior is implemented, deployed, or accepted.
