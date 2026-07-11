# OCLAW-PMEM-005 BFF Handoff Follow-up 6

- **Sidecar Task ID**: `OCLAW-PMEM-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-6`
- **Parent Task**: `OCLAW-PMEM-005`
- **Parent Owner**: `Codex`
- **Sidecar Owner**: `Codex`
- **Sidecar Reviewer**: `Antigravity`
- **Helper Kind**: `bff_handoff_packet`
- **Generated**: 2026-07-11
- **Mutates Canonical**: `no`

This packet is support material for parent composition. It does not implement
or define canonical Memory Plane, BFF, OpenClaw, provider, materialization,
frontend, gate, registry, or governance behavior.

## 1. Verified BFF Query Gap

At this branch tip, both `bff_get_persona_memory` and
`_pm12_memory_items_for_persona` in `services/control-plane/bff/main.py`
discover `read_store.list_memory_updates_for_persona` with `getattr`. When the
reader is absent, each path produces an ordinary empty collection. The public
persona-memory route therefore cannot distinguish a completed authorized query
with zero canonical entries from a missing integration.

The parent must not use that ambiguous empty response as retrieval proof. A
successful empty state requires an explicit completed canonical-source
observation. Missing reader/source, timeout, invalid response, authorization
failure, and query failure remain non-success outcomes. Runtime profile and
provider readiness/auth projections are inputs only; they do not prove
observed OpenClaw identity or a required live provider call.

## 2. Parent-Owned Server Handoff

The parent may add a gate-specific BFF projection or compose existing reads on
the server. This sidecar deliberately does not prescribe a route or DTO. The
resulting server boundary should preserve the following meanings:

| Observation | Minimum safe evidence | Blocking condition |
|---|---|---|
| Deployment | Exercised BFF and OpenClaw/adapter revision plus observation time | Revision is absent or does not identify the exercised deployment. |
| Persona runtime | Requested persona and observed persona/model/workspace identity | Desired profile is substituted for observation, or identities drift. |
| Provider | Required provider/model live invocation result | Readiness, auth, or fallback is substituted for the required call. |
| Canonical retrieval | Completed-source status, authorized persona, stable memory/source IDs | Source is missing/unavailable, response invalid, or private scope is foreign. |
| Materialization | Workspace identity, generation, canonical source IDs read back | File presence is the only evidence, IDs differ, or generations are mixed. |
| Isolation | Subject persona, safe foreign fixture ID, BFF/workspace verdict | A foreign private ID or content reaches either boundary. |

All observations used for a final verdict must belong to one opaque
server-owned verification run and one freshness policy. A retry that can
change provider, runtime, retrieval, or materialization correlation starts a
new run or atomically replaces the snapshot. The server emits the final
verdict and reasons; the browser must not join cards from different runs or
recompute pass.

## 3. Frontend Handoff

Frontend implementation belongs in `ajoe734/execute-plans`, not in this repo.
It must call Pantheon BFF routes only. Hosted dev proof must use live BFF mode,
the Pantheon-owned dev BFF origin, strict fallback, and safe write defaults.

| Server meaning | Operator presentation |
|---|---|
| No verification run | Offer start action; show no verdict. |
| Run incomplete | Show progress, run ID, and unavailable final verdict. |
| Canonical query completed with zero authorized items | Show valid empty with source and observation time. |
| Canonical source missing, unavailable, failed, or unauthorized | Show blocking reason and remediation owner; never empty success. |
| Required live smoke failed or stale | Show provider failure even when auth or fallback is healthy. |
| Desired/observed runtime drift | Show a safe identity diff and block pass. |
| Workspace IDs or generation mismatch | Label derived-cache failure and block pass. |
| Isolation failure | Show critical stop and safe fixture/boundary only; suppress private payload. |
| Fresh correlated server pass | Show server verdict, revisions, timestamp, and sanitized evidence links. |

The browser must not call Memory Plane, provider APIs, OpenClaw adapter
endpoints, or VM workspace files directly.

## 4. Parent Acceptance Cases

Before parent closeout, focused tests and hosted evidence should cover:

- completed authorized canonical retrieval with zero entries as valid empty;
- absent reader, timeout, malformed response, authorization failure, and query
  failure as explicit non-success states;
- auth-ready with failed or missing required live provider smoke;
- fallback success while the required primary provider path fails;
- desired profile without matching observed OpenClaw identity;
- workspace presence without canonical source-ID readback;
- matching IDs from different generations as mixed/stale evidence;
- a foreign private fixture at BFF or workspace boundary as a critical failure
  without payload disclosure; and
- one fresh run where provider, observed runtime, canonical retrieval,
  materialization, and isolation correlate to a server-owned pass.

Component tests can prove envelope and correlation behavior. They do not
replace hosted live invocation, observed runtime identity, workspace readback,
or the cross-persona negative probe.

## 5. Composition Record and Non-Claims

`Codex`, as parent owner, should compose only reviewer-accepted outputs from
`OCLAW-PMEM-002`, `003`, and `004`, then retain child PRs and merge SHAs,
deployed BFF/frontend revisions, exact commands and timestamps, sanitized
positive and negative snapshots, freshness policy, and residual risks.

`Antigravity` reviews this packet for factual accuracy, support-only scope,
fail-closed empty/unavailable semantics, Memory Plane authority, server-owned
correlation, derived-cache labeling, and mandatory live smoke and isolation
evidence. Approval makes the packet available for parent composition only.
It does not claim the current persona-memory response is canonical, readiness
proves provider usability, desired configuration proves runtime convergence,
workspace existence proves materialization, component tests are hosted proof,
or any described BFF/frontend behavior is implemented or deployed.
