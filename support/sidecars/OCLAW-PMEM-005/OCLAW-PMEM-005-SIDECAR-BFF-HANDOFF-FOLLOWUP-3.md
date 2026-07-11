# OCLAW-PMEM-005 BFF Handoff Follow-up 3

**Sidecar Task ID**: `OCLAW-PMEM-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-3`
**Parent Task**: `OCLAW-PMEM-005`
**Parent Owner**: `Codex`
**Sidecar Owner**: `Codex2`
**Sidecar Reviewer**: `Antigravity`
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-07-11
**Mutates Canonical**: `no`

This packet is support-only. It refines the BFF query gap, operator journey,
frontend state model, and parent composition boundary. It does not change the
Memory Plane, BFF, OpenClaw adapter, workspace materializer, provider policy,
frontend, dev gates, registry, or canonical contracts.

## 1. Verified Current-State Gap

`GET /bff/personas/{persona_id}/memory` in
`services/control-plane/bff/main.py` still resolves
`read_store.list_memory_updates_for_persona` through `getattr`. If that reader
is absent, the route emits its normal success envelope with an empty item list.
The response therefore cannot distinguish:

- an authorized canonical Memory Plane query that completed with zero items;
- a missing BFF reader or unconfigured Memory Plane integration; or
- a query that was never made.

`OCLAW-PMEM-005` must not accept that ambiguous empty response as canonical
retrieval proof. Missing configuration, timeout, authorization failure,
malformed upstream data, and unavailable source must remain explicit failed or
unavailable observations.

The existing persona runtime-profile projection, assistant provider
usage/readiness observations, and provider reauthentication routes are useful
inputs. None proves an actual provider invocation, observed OpenClaw runtime
identity, or canonical memory materialization by itself.

## 2. Parent-Consumable Query Shape

The parent may introduce a gate-specific server projection or correlate
existing reads server-side. This sidecar does not prescribe a canonical DTO,
but the resulting observation must preserve these meanings:

| Field group | Required meaning | Must not be inferred from |
|---|---|---|
| Correlation | One opaque run ID, observation time, deployed commit, and materialization generation | Browser clock or unrelated requests |
| Canonical query | `available`, `unavailable`, or `failed`; Memory Plane source; authorized persona; stable memory/source IDs | Empty array alone |
| Provider | Required provider/model, auth result, and a separate live-smoke result | Credential mount, quota, reauth, or fallback success |
| Runtime | Desired profile and observed OpenClaw persona/model/workspace identity | Desired BFF profile alone |
| Materialization | Derived-cache label, observed generation, and canonical source IDs read back | Workspace/file existence alone |
| Isolation | Negative-probe verdict and safe fixture IDs only | Absence of a UI row or redacted leak content |
| Verdict | Server-owned pass/fail with reason codes and freshness | Client aggregation of green cards |

An `available` canonical observation may legitimately contain zero items. An
`unavailable` or `failed` observation must never be normalized into that empty
success state. Every source-ID comparison must use the same run and generation;
otherwise the evidence is stale or mixed and the gate fails.

## 3. Operator Journey

1. Select one persona and start a fresh verification run. Display the run ID,
   observation time, deployed commit, and generation before any verdict.
2. Compare desired runtime profile with observed OpenClaw persona, model route,
   workspace, and generation. Missing or drifted observation blocks the run.
3. Show provider authentication and required live smoke as separate checks.
   Authentication-ready plus smoke-pending is not pass. A successful fallback
   does not repair a failed required-primary check.
4. Query canonical memory through the BFF. Render `available with 0 items`
   separately from `unavailable` and show only safe stable IDs and scope.
5. Compare canonical source IDs with workspace readback from the same
   generation. Label workspace memory as derived cache, never authority.
6. Run a second-persona/private-memory negative probe. Show verdict, safe
   fixture identifiers, and remediation owner without private content.
7. Publish only the server-owned verdict. Archive sanitized evidence, exact
   commands, timestamps, and deployed revisions for parent closeout.

## 4. Frontend State Model

Frontend implementation belongs in `ajoe734/execute-plans` and must call only
Pantheon BFF routes. Hosted dev validation must use live BFF mode, the Pantheon
dev BFF origin, and strict fallback.

| UI state | Entry condition | Operator presentation |
|---|---|---|
| `not_started` | No run ID | Start verification; no implied result |
| `verifying` | Fresh run has incomplete observations | Per-check progress; overall verdict withheld |
| `available_empty` | Canonical query completed and authorized with zero items | Valid empty state with source and observation time |
| `source_unavailable` | Reader/source missing, timeout, or invalid upstream | Blocking source failure; retry/remediation action |
| `runtime_drift` | Desired and observed runtime identities differ | Blocking diff with safe identifiers |
| `provider_failed` | Required live smoke failed or is stale | Failure remains even when auth/fallback is healthy |
| `isolation_failed` | Foreign private ID/content reached either boundary | Critical failure; suppress content and stop pass publication |
| `passed` | Server verdict confirms every fresh correlated observation | Evidence links and revisions |

The frontend must not call Memory Plane, provider APIs, adapter endpoints, or VM
workspace files directly. It must not reconstruct a pass verdict, silently use
fixtures, or downgrade unavailable into an empty-memory presentation.

## 5. Acceptance and Negative Checks

The parent gate and its focused tests should demonstrate:

- canonical success with zero authorized items renders `available_empty`;
- absent optional reader, timeout, and malformed upstream each fail closed;
- a foreign private-memory request is denied and no content is exposed;
- provider auth-ready plus failed/missing live smoke remains failed;
- required-primary failure remains failed after fallback success;
- desired runtime profile without matching observed identity fails;
- workspace existence without canonical source IDs fails;
- equal IDs from different generations fail as mixed evidence;
- any foreign private ID in BFF or workspace evidence fails isolation; and
- one fresh run with matching identities, IDs, generation, smoke, and isolation
  produces a server-owned pass.

Unit and component tests can prove response semantics and correlation rules.
They do not replace hosted provider invocation, observed OpenClaw identity,
workspace readback, or the negative cross-persona probe.

## 6. Parent Absorption Checklist

Before closing `OCLAW-PMEM-005`, the parent owner should retain:

- accepted child PRs and merge SHAs for `OCLAW-PMEM-002`, `003`, and `004`;
- BFF and execute-plans deployed commit IDs;
- exact local and hosted commands, timestamps, and sanitized outputs;
- required provider live-smoke evidence distinct from auth/readiness;
- desired-versus-observed runtime identity for the same run;
- canonical-to-materialized source-ID equality for the same generation;
- an explicit unavailable-versus-empty negative test;
- a second-persona/private-memory isolation negative test; and
- residual risks, evidence freshness, and required paths not exercised.

`Codex`, as parent owner, decides whether to absorb this support packet and owns
all executable gates, runtime changes, and canonical changes. `Antigravity`
reviews this sidecar only for accuracy, support-only scope, fail-closed query
semantics, Memory Plane authority, derived-cache labeling, and mandatory live
smoke/isolation evidence.

## 7. Non-Claims

This packet does not claim that the current persona-memory BFF response is
canonical, that readiness proves provider usability, that desired runtime
configuration proves convergence, that unit tests are hosted evidence, or that
the described BFF/frontend work is implemented or deployed. Reviewer approval
only makes the packet available for parent composition.
