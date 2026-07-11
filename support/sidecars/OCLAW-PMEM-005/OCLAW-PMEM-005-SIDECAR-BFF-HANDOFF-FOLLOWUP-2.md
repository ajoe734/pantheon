# OCLAW-PMEM-005 BFF Handoff Follow-up 2

**Sidecar Task ID**: `OCLAW-PMEM-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`  
**Parent Task**: `OCLAW-PMEM-005`  
**Parent Owner**: `Codex`  
**Sidecar Owner**: `Codex2`  
**Sidecar Reviewer**: `Antigravity`  
**Helper Kind**: `bff_handoff_packet`  
**Generated**: 2026-07-11  
**Mutates Canonical**: `no`

This follow-up is support material only. It verifies current BFF integration
points and turns the earlier handoff into a parent-ready implementation and
operator acceptance checklist. It does not change BFF or frontend code,
Memory Plane authority, OpenClaw runtime/materialization contracts, provider
policy, registry behavior, dev gates, or governance truth.

## 1. Current-Branch Findings

### Persona memory still cannot distinguish unavailable from empty

`GET /bff/personas/{persona_id}/memory` in
`services/control-plane/bff/main.py` still discovers
`read_store.list_memory_updates_for_persona` with `getattr`. When the reader is
absent, the route returns its ordinary success envelope with an empty list.
That response cannot prove that the canonical Memory Plane was queried, and it
is observationally equivalent to an authorized canonical query returning zero
entries.

For the parent gate, missing reader configuration, timeout, malformed upstream
response, and authorization failure must be explicit non-success observations.
Only a completed canonical query may produce `available` with zero items.

### Runtime profile is reusable but not live convergence proof

`GET /bff/personas/{persona_id}/runtime-profile` already builds the persona
runtime projection and has contract coverage in
`test_bff_strategy_persona_contract.py`. The parent should reuse that route or
its builder for desired persona/model/workspace identity. A desired profile
alone does not prove that OpenClaw observed the same persona, model route,
workspace, or materialization generation.

### Provider readiness must remain separate from live invocation

Existing assistant provider reads expose useful auth/readiness observations,
usage coverage, and reauth lifecycle. They do not permit the parent to infer a
successful live persona/provider invocation from credential mount, auth-ready,
quota metadata, or completed reauth. A required primary provider remains
failed when its live smoke fails, even if a fallback provider succeeds.

## 2. Parent-Owned BFF Projection

The parent may implement one gate snapshot or correlate existing BFF reads,
but correlation and the final verdict belong on the server. The browser must
not assemble a pass from individually green fields.

Minimum independently observed groups are:

| Group | Required evidence | Fail-closed condition |
|---|---|---|
| Run identity | Opaque run ID, snapshot time, deployed commit/generation | Evidence belongs to different or stale runs. |
| Provider | Required provider/model, auth observation, separate live-smoke result | Auth ready but smoke missing, stale, failed, or satisfied only by fallback. |
| Runtime | Desired profile plus observed OpenClaw persona/model/workspace/generation | Desired and observed identities differ or observation is absent. |
| Canonical memory | Memory Plane source, availability, authorized persona, stable memory/source IDs | Source unavailable, ambiguous empty result, invalid envelope, or foreign private entry. |
| Materialization | Derived-cache label, generation/time, canonical source IDs read back from workspace evidence | Workspace merely exists, IDs are missing/mismatched, or generation differs. |
| Isolation | Negative probe using another persona/private fixture, reporting safe IDs only | Any foreign private ID/content reaches response or materialization. |

Use existing BFF envelope and enum conventions when implementing this. This
packet intentionally does not promote a new canonical DTO.

## 3. Operator Journey and UI States

1. Start a fresh verification run for one persona and display its run ID,
   snapshot time, and deployed commit/generation.
2. Show desired runtime profile beside observed OpenClaw identity. Drift is a
   blocking state, not informational copy.
3. Show provider auth and live smoke separately. Auth-ready with pending smoke
   remains `verification required`; reauth success returns to verifying.
4. Query canonical memory through the BFF. Render `available, 0 items`
   differently from `unavailable`, and show safe canonical IDs when present.
5. Compare canonical IDs with the derived workspace materialization from the
   same generation. Label workspace output as cache, never source of truth.
6. Run a cross-persona isolation probe. Render only pass/fail, safe fixture
   identifiers, and remediation ownership; never render leaked private content.
7. Publish pass only from the server-owned verdict when every required
   observation is fresh and correlated. Archive sanitized evidence and exact
   validation commands for parent closeout.

Frontend implementation belongs in `ajoe734/execute-plans`. It must call the
Pantheon BFF only, use live BFF mode with strict fallback for hosted dev proof,
and must not read Memory Plane, provider APIs, adapter routes, or VM workspace
files directly.

## 4. Focused Acceptance Matrix

| Scenario | Expected parent gate/UI result |
|---|---|
| Canonical query succeeds with zero authorized items | Available empty state; other checks continue. |
| Optional reader missing or Memory Plane times out | Source unavailable; gate fails. |
| Auth ready and required live smoke fails | Provider unusable for this run; gate fails. |
| Primary smoke fails and fallback succeeds | Required primary check remains failed. |
| Desired profile exists but observed OpenClaw identity is absent/drifted | Runtime convergence fails. |
| Workspace exists without canonical source IDs | Materialization fails. |
| Canonical and materialized IDs match but generations differ | Evidence is stale/mixed; gate fails. |
| Foreign private ID appears in BFF or workspace evidence | Critical isolation failure; no content disclosure. |
| Every observation passes for one fresh run/generation | Server verdict passes; UI renders evidence links. |

Focused component tests should cover available-empty versus unavailable,
timeout/invalid upstream response, private-scope denial, ID equality, stale
generation rejection, auth-ready plus failed smoke, and primary-versus-fallback
semantics. Component tests do not replace hosted provider invocation, observed
OpenClaw identity, or workspace readback.

## 5. Parent Closeout Handoff

Before closing `OCLAW-PMEM-005`, the parent should retain:

- accepted child PR numbers and merge SHAs for `OCLAW-PMEM-002`, `003`, and
  `004`;
- deployed BFF and frontend commit IDs;
- exact local and hosted commands with timestamps;
- one sanitized passing snapshot for each required provider/persona path;
- a negative auth-ready/live-smoke-failed result;
- a negative cross-persona isolation result;
- canonical-to-materialized source-ID equality from the same generation; and
- residual risks, probe freshness, and required provider paths not exercised.

The parent owner should compose this packet with accepted child outputs rather
than copy or redefine their contracts. `Codex` owns executable gates and any
canonical/runtime changes. `Antigravity` should review only whether this packet
is accurate, support-only, preserves Memory Plane authority, labels workspace
memory as derived cache, and keeps live smoke and isolation mandatory.

## 6. Non-Claims

This packet does not claim the current persona-memory response is canonical,
that provider readiness proves usability, that desired runtime configuration
proves convergence, that existing unit tests are hosted evidence, or that any
BFF/frontend implementation has been deployed. Reviewer approval permits the
parent owner to consume the support material; it does not promote this sketch
to canonical contract truth.
