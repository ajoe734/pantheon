# OCLAW-PMEM-004 BFF Handoff Follow-up 2

**Sidecar Task ID**: `OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`
**Parent Task**: `OCLAW-PMEM-004`
**Parent Owner**: `Claude2`
**Sidecar Owner**: `Codex2`
**Sidecar Reviewer**: `Claude`
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-07-11
**Mutates Canonical**: `no`

This follow-up is support material for the parent owner. It validates concrete
integration points against the current task branch and narrows the first
sidecar packet into an implementation-ready BFF/frontend handoff. It does not
change Memory Plane authority, runtime-profile contracts, BFF code, frontend
code, provider policy, or governance.

## 1. Verified Current Gaps

### Persona memory falsely resembles a valid empty result

`GET /bff/personas/{persona_id}/memory` in
`services/control-plane/bff/main.py` obtains
`read_store.list_memory_updates_for_persona` with `getattr`. When that reader is
absent, the route returns HTTP 200 with empty `data` and `items` plus ordinary
`persona_memory` surface metadata. The browser cannot distinguish:

- canonical Memory Plane returned zero authorized entries; from
- the BFF has no canonical Memory Plane reader at all.

Parent implementation should replace this optional-reader behavior with a
Memory Plane facade/client result. Until that source is configured or readable,
the response must carry `status: unavailable`, a stable reason, and source
identity. An available empty collection must explicitly say `status: available`
and total zero.

### Runtime profile is already a separate reusable read

`GET /bff/personas/{persona_id}/runtime-profile` already calls
`build_persona_runtime_profile(...)`, fails with 422 for invalid model refs, and
has focused tests in `test_bff_strategy_persona_contract.py`. The parent should
compose this existing projection into persona detail rather than recreate route
resolution in a new BFF helper or the frontend.

### Provider reads exist but are not an operator-complete pool projection

The following BFF routes already provide useful pieces:

| Existing route | Reusable evidence | Remaining parent gap |
|---|---|---|
| `GET /bff/assistant/providers?auth_probe=true` | Adapter-backed provider/auth readiness | Define freshness and live-smoke evidence separately from credential/auth state. |
| `GET /bff/assistant/providers/usage-summary` | BFF-observed calls, provider quota snapshot, explicit unknown quota policy | Add dependency completeness and avoid treating observed BFF calls as total provider usage. |
| `POST /bff/assistant/provider/reauth` | Role-gated, sanitized start response | Normalize allowed next action and lifecycle state for the UI. |
| `GET /bff/assistant/provider/reauth/{session_id}` | Sanitized polling result | Preserve terminal reason and advertise readiness recheck requirement. |
| `POST /bff/assistant/provider/reauth/{session_id}/code` | Required code validation and sanitized forwarding | UI must render only when the status advertises code entry. |

`_assistant_provider_list()` already degrades adapter failures instead of
inventing readiness. `_assistant_provider_usage_summary()` also preserves
`quota.source: not_configured` and documents that missing quota means unknown,
not zero. Those semantics should survive composition unchanged.

## 2. Minimal Parent-Owned Query Composition

The parent can implement one operator projection or compose parallel reads, but
the BFF must own all joins and usability decisions. A provider row needs these
independent evidence groups:

```json
{
  "provider": "claude",
  "auth": {"status": "ready", "observed_at": "..."},
  "live_smoke": {"status": "failed", "completed_at": "...", "reason": "..."},
  "usability": {"status": "degraded", "reason": "live_smoke_failed"},
  "quota": {"source": "not_configured", "remaining": null},
  "observed_usage": {
    "source": "management_ai_bff_audit",
    "coverage": "bff_observed_management_ai_only"
  },
  "persona_dependencies": {"complete": false, "count": null, "items": []},
  "reauth": {"status": "idle", "allowed_actions": ["start"]}
}
```

This remains a sketch, not canonical schema. Required truth rules are:

1. `usability` is server-computed from explicit evidence; the browser does not
   infer it from mounts, auth, model refs, quota, or reauth completion.
2. Dependency rows are derived from canonical persona runtime profiles. If the
   profile inventory is incomplete, `complete` is false and `count` is not a
   definitive zero.
3. BFF-observed usage retains its limited coverage label.
4. Reauth success moves the UI to `verifying`; only a fresh readiness/live
   smoke result can move it to usable.

## 3. Persona Detail Composition

Keep runtime routing, canonical memory, and workspace materialization visibly
separate:

| Panel field | Source | Required degraded behavior |
|---|---|---|
| Runtime/model route | Existing runtime-profile route | Preserve profile error; do not invent browser fallback. |
| Canonical memory entries | Memory Plane retrieval facade | Unavailable is not an empty-state success. Enforce persona-private scope. |
| Last materialization | Accepted `OCLAW-PMEM-003` bridge evidence | Missing evidence is unknown; workspace presence is not success. |
| Workspace/cache ref | Runtime profile/materialization evidence | Label derived cache, never canonical memory. |

The memory projection should retain canonical entry IDs, scope, retrieval time,
materialization generation/time, and sanitized failure reason. It must never
return another persona's private entries.

## 4. Recommended Implementation Order

1. Wait for and consume accepted `OCLAW-PMEM-002` runtime-profile and
   `OCLAW-PMEM-003` bridge outputs; do not copy their contracts into BFF.
2. Replace the optional persona-memory reader with a canonical Memory Plane
   facade and explicit available/unavailable metadata.
3. Add focused BFF tests for available-empty, source-unavailable, timeout, and
   cross-persona private-memory denial.
4. Compose provider auth/smoke, quota/usage, runtime-profile dependencies, and
   reauth state in the BFF.
5. Add BFF tests proving auth-ready plus failed/stale/missing smoke is not
   usable, and incomplete dependency inventory is not zero.
6. Hand the stable BFF DTO to `ajoe734/execute-plans`; frontend work belongs in
   that repository, not inside Pantheon.
7. Add frontend tests for independent degraded states, code entry, post-reauth
   verifying, and available-empty versus unavailable memory copy.

## 5. Parent Acceptance Checklist

- [ ] Persona memory reads canonical Memory Plane or reports a precise
  unavailable source and reason.
- [ ] Available-empty and unavailable responses are observably different.
- [ ] Private persona memory cannot cross persona boundaries.
- [ ] Runtime profile continues to use the existing contract and fail-closed
  validation.
- [ ] Provider auth, live smoke, quota source, observed-usage coverage,
  dependency completeness, and reauth state remain independent.
- [ ] Auth-ready or reauth-succeeded alone never produces usable.
- [ ] Code entry is exposed only for an advertised `awaiting_code` state.
- [ ] Successful reauth triggers a new readiness/live-smoke check.
- [ ] Frontend calls BFF only and labels workspace materialization as derived
  cache.

## 6. Non-Claims and Handoff

This packet does not claim that dependencies are complete, that the current BFF
memory response is canonical, that provider auth proves live usability, or that
frontend work is implemented or deployed. `Claude2`, as parent owner, decides
whether to absorb these findings and owns all canonical/runtime changes.
`Claude` approved this sidecar after checking its route and function claims
against the current BFF code and confirming the diff remains support-only.
The parent owner still decides whether to absorb the packet; approval does not
promote this sketch into canonical Memory Plane or BFF contract truth.

## 7. Closeout Evidence

Owner finalization rechecked the approved packet on 2026-07-11 with:

- `git diff --check origin/dev...HEAD`
- focused source searches for the persona-memory, runtime-profile, provider
  readiness/usage, and reauth integration points named above
- `git diff --name-status origin/dev...HEAD` to confirm only task-scoped support
  artifacts changed

Reviewer findings are preserved in
`support/reviews/OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-review-claude.md`.
