# OCLAW-PMEM-005 BFF Handoff Follow-up 4

**Sidecar Task ID**: `OCLAW-PMEM-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-4`  
**Parent Task**: `OCLAW-PMEM-005`  
**Parent Owner**: `Codex`  
**Sidecar Owner**: `Codex2`  
**Sidecar Reviewer**: `Antigravity`  
**Helper Kind**: `bff_handoff_packet`  
**Generated**: 2026-07-11  
**Mutates Canonical**: `no`

This packet is support-only. It narrows the BFF/frontend handoff to evidence
correlation, freshness, and operator-safe presentation. It does not modify or
define canonical Memory Plane, BFF, OpenClaw, materialization, provider,
frontend, gate, registry, or governance behavior.

## 1. Current Gap the Parent Must Close

The current `GET /bff/personas/{persona_id}/memory` implementation in
`services/control-plane/bff/main.py` discovers
`read_store.list_memory_updates_for_persona` with `getattr`. When no reader is
configured, it returns the normal successful response with zero items. This is
indistinguishable from a completed, authorized canonical query whose result is
legitimately empty.

Consequently, an empty list is not retrieval evidence. The parent gate must
retain an explicit source observation and fail closed for missing integration,
timeout, authorization failure, malformed upstream response, or unavailable
Memory Plane. Only a completed canonical query may be represented as
`available_empty`.

The existing runtime-profile and provider readiness/authentication projections
are useful inputs, but they do not prove observed OpenClaw identity or a live
provider invocation. Those observations must remain separate.

## 2. Evidence Correlation Rules

The server-owned gate snapshot should bind every observation to one verification
run. This packet does not prescribe a canonical DTO; it records the minimum
correlation semantics the parent and frontend handoff must preserve.

| Evidence | Required correlation keys | Reject when |
|---|---|---|
| Deployment | BFF revision, OpenClaw/adapter revision or generation, observation time | Revision is absent or differs from the exercised deployment. |
| Persona/runtime | Run ID, requested persona ID, observed persona/model/workspace identity | Desired profile is substituted for observed identity, or identities drift. |
| Provider smoke | Run ID, required provider/model, invocation time, result | Auth/readiness or fallback success is substituted for the required live call. |
| Canonical retrieval | Run ID, authorized persona ID, source status, stable memory/source IDs | Empty items lack a completed-source observation, or private scope is foreign. |
| Materialization | Run ID, generation, workspace identity, canonical source IDs read back | File existence is the only proof, IDs differ, or generation is mixed. |
| Isolation probe | Run ID, subject persona, foreign fixture ID, safe verdict | Foreign private ID/content reaches either BFF or workspace evidence. |

Freshness is a gate input, not UI decoration. The parent should define a single
server-side freshness policy for a run and emit an explicit stale reason. The
browser must not compare its clock, merge independently fetched observations,
or repair stale evidence by retrying only one green card. A retry that can
change correlation starts a new run or replaces the entire server snapshot.

## 3. BFF-to-Frontend Handoff

Frontend work belongs in `ajoe734/execute-plans`. It must use Pantheon BFF
routes only. Hosted dev proof must build with live BFF mode, the Pantheon-owned
dev BFF origin, and strict fallback.

The frontend needs these server-owned distinctions:

| Server meaning | UI treatment | Forbidden presentation |
|---|---|---|
| No run | Offer start action; show no verdict | Default green or fixture-backed result |
| Run incomplete | Show verification progress and run identity | Partial overall pass |
| Canonical available, zero items | Valid empty result with source/time | “Memory unavailable” |
| Canonical unavailable/failed | Blocking remediation and retry | Empty success list |
| Required live smoke failed/stale | Blocking provider result | Healthy because auth or fallback passed |
| Desired/observed runtime drift | Blocking identity diff using safe identifiers | Desired profile shown as observed truth |
| Materialization IDs/generation mismatch | Blocking derived-cache failure | Workspace existence treated as proof |
| Isolation failure | Critical stop; suppress private content | Leaked content rendered for diagnosis |
| Correlated server verdict passed | Evidence links, revisions, run time | Client-recomputed pass |

The UI may expose safe identifiers, reason codes, remediation owner, and links
to sanitized evidence. It must not call Memory Plane, provider APIs, adapter
routes, or VM files directly, and must not render private payloads from a
negative isolation test.

## 4. Parent Implementation and Test Handoff

The parent may implement a gate-specific BFF projection or correlate existing
reads server-side. Whichever approach it chooses should cover these focused
cases:

- completed canonical query with zero authorized items is `available_empty`;
- reader absent, timeout, malformed response, and authorization failure remain
  distinct non-success observations;
- required provider auth-ready plus failed/missing live smoke fails;
- fallback success does not repair required-primary failure;
- desired profile without matching observed OpenClaw identity fails;
- canonical and workspace source IDs must match within the same generation;
- equal IDs from different generations fail as mixed evidence;
- workspace existence without source-ID readback fails;
- any foreign private ID at either boundary fails isolation without disclosing
  content; and
- only one fresh, fully correlated run can produce the server-owned pass.

Component tests can validate envelopes, reason codes, and correlation rules.
They do not replace hosted live invocation, observed runtime identity,
workspace readback, or the cross-persona negative probe.

## 5. Parent Absorption Order

Before closing `OCLAW-PMEM-005`, `Codex` should:

1. absorb reviewer-accepted outputs from `OCLAW-PMEM-002`, `003`, and `004`
   without redefining their contracts;
2. close the ambiguous empty-versus-unavailable BFF query behavior;
3. correlate provider, observed runtime, canonical retrieval, materialization,
   and isolation evidence into one server-owned run verdict;
4. hand the resulting BFF meanings and reason codes to `execute-plans` without
   copying frontend source into Pantheon;
5. retain child PRs and merge SHAs, deployed revisions, exact commands and
   timestamps, sanitized positive/negative snapshots, freshness policy, and
   residual risks in the parent closeout packet.

`Antigravity` should review this sidecar only for factual accuracy, support-only
scope, fail-closed empty/unavailable semantics, server-owned correlation,
Memory Plane authority, derived-cache labeling, and mandatory live smoke and
isolation evidence. The parent owner decides whether and how to compose it.

## 6. Non-Claims

This packet does not claim the current persona-memory route is canonical, that
readiness proves provider usability, that desired runtime configuration proves
convergence, that workspace presence proves materialization, that component
tests are hosted evidence, or that any described BFF/frontend work is
implemented or deployed. Reviewer acceptance makes this support packet
available for parent composition only.
