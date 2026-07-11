# OCLAW-PMEM-004 BFF and Frontend Handoff Packet

**Sidecar Task ID**: `OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF`  
**Parent Task**: `OCLAW-PMEM-004`  
**Parent Owner**: `Claude2`  
**Parent Reviewer**: `Codex`  
**Sidecar Owner**: `Codex`  
**Sidecar Reviewer**: `Antigravity`  
**Helper Kind**: `bff_handoff_packet`  
**Generated**: 2026-07-11  
**Mutates Canonical**: `no`

This is support material only. It does not change L1 truth, Memory Plane,
persona runtime-profile contracts, OpenClaw synchronization, provider
readiness, BFF implementation, frontend code, or governance. The parent owner
decides whether and how to absorb it into `OCLAW-PMEM-004`.

## 1. Delivery Boundary

The operator needs two related but distinct views:

1. Persona detail explains the persona's runtime route and which canonical
   memory was materialized for it.
2. LLM Auth explains whether each shared provider is authenticated, recently
   proven usable, quota-observable, and recoverable through reauth.

The UI must not collapse these into one `ready` boolean. A mounted credential
or configured model ref is not a successful provider smoke, and an OpenClaw
workspace cache is not canonical memory.

## 2. Existing Integration Points to Reuse

| Concern | Existing repository point | Parent integration guidance |
|---|---|---|
| Persona runtime profile | `GET /bff/personas/{persona_id}/runtime-profile`; `services/persona/runtime_profile.py` | Reuse the profile projection. Do not recreate model-routing resolution in BFF or the browser. |
| Persona memory | `GET /bff/personas/{persona_id}/memory` currently calls an optional `list_memory_updates_for_persona` reader | Replace the disconnected optional read with a Memory Plane client/facade or return a precise unavailable surface. Do not silently return an authoritative empty list when the source cannot be read. |
| Canonical retrieval | `GET /api/memory/retrieve`; `integrations/openclaw/persona_memory_bridge.py` | Preserve canonical entry IDs, scope, retrieval/materialization timestamps, and source status in the operator-safe projection. Workspace files remain derived cache. |
| Provider readiness | `GET /bff/assistant/provider/readiness` through `assistant/routes.py` and `openclaw_ops_client.py` | Reuse the adapter-backed probe, but project auth and live-smoke evidence separately. |
| Provider usage/quota | Existing assistant provider usage summary and tests in `tests/test_management_nl_assistant_provider.py` | Keep `quota.source`; unknown/not-configured quota must remain visibly unknown, not zero or healthy. |
| Reauth | `POST /bff/assistant/provider/reauth`, `GET /bff/assistant/provider/reauth/{session_id}`, `POST .../{session_id}/code` | Reuse the role/MFA-gated, redacted flow. Poll terminal state and rerun readiness after completion. Never expose credentials or provider tokens. |

## 3. BFF Query Gap Matrix

| Query / DTO need | Current evidence | Required behavior for parent | Failure semantics |
|---|---|---|---|
| Persona runtime profile | Read route and contract tests exist. | Return route mode, primary/fallback model refs, workspace ref, sync generation, memory policy, and source refs. | Invalid/unknown model refs fail closed; UI labels routing degraded. |
| Canonical persona memory list | Current BFF route can fall back to an absent optional reader. | Read Memory Plane for the requested persona and return operator-safe summaries with canonical IDs and scope. | `unavailable` with reason/source when Memory Plane is unreachable; a valid empty result is `available` with zero items. |
| Last materialization | Bridge writes traceable derived context, but the BFF persona response does not yet prove the latest result. | Expose latest attempt/result, generation timestamp, source entry IDs/count, bounded-cache location/ref, and failure reason. | Never infer success from workspace/mount existence. Missing evidence is `unknown` or `unavailable`. |
| Provider pool overview | Assistant readiness is available, but persona dependency and complete pool projection are not established by this packet. | One row per provider with auth state, last live smoke, quota/usage source, dependent personas, and reauth state. | Preserve per-field unavailable reasons; do not promote partial data to provider-ready. |
| Persona dependencies by provider | Runtime profiles contain model routing. | Aggregate server-side from canonical profiles and include persona ID/name plus primary/fallback relation. | If profile inventory is incomplete, mark dependency count incomplete rather than presenting a definitive zero. |
| Live provider smoke | Readiness adapter is the existing source. | Include attempted/completed timestamps, outcome, probe identity/type, and sanitized reason. | Stale, missing, or failed smoke prevents a `usable` claim even when auth is ready. |
| Quota and usage | Existing usage summary distinguishes provider snapshot and not-configured. | Return window, used/remaining/limit where known, observed timestamp, and source. | Unknown values remain null/unknown; never convert them to zero. |
| Reauth lifecycle | Start/status/code routes already exist. | Normalize `idle`, `starting`, `awaiting_user`, `awaiting_code`, `verifying`, `succeeded`, `failed`, `expired`; advertise allowed next action. | Keep session tracking IDs but redact secrets; terminal success triggers a fresh readiness query. |

## 4. Recommended Projection Shapes

These are handoff sketches, not canonical schema. The parent owner may compose
them with existing envelopes and naming conventions.

### Persona detail additions

```json
{
  "runtime_profile": {
    "status": "available",
    "model_routing": {
      "mode": "preferred_pool_model",
      "primary": "anthropic/claude-sonnet",
      "fallbacks": ["openai/codex"]
    },
    "workspace_ref": "openclaw/persona-123",
    "sync_generation": "generation-ref"
  },
  "memory": {
    "source": "canonical_memory_plane",
    "status": "available",
    "items": [{"memory_id": "pmem-123", "scope": "persona", "summary": "..."}],
    "last_materialization": {
      "status": "succeeded",
      "generated_at": "2026-07-11T00:00:00Z",
      "source_memory_ids": ["pmem-123"]
    }
  }
}
```

### Provider pool row

```json
{
  "provider": "claude",
  "auth": {"status": "ready", "observed_at": "2026-07-11T00:00:00Z"},
  "live_smoke": {"status": "failed", "completed_at": "2026-07-11T00:01:00Z", "reason": "sanitized reason"},
  "usability": {"status": "degraded", "reason": "live_smoke_failed"},
  "quota": {"source": "not_configured", "limit": null, "used": null, "remaining": null},
  "persona_dependencies": {"complete": true, "count": 2, "items": []},
  "reauth": {"status": "idle", "allowed_actions": ["start"]}
}
```

`usability.status` must be computed by the BFF from explicit evidence. The
frontend may render it, but must not derive it from auth, mount, quota, or model
selection alone.

## 5. Operator Journey

### Persona runtime and memory

1. Open persona detail and request runtime profile plus canonical memory.
2. Show model route, provider relationship, workspace ref, and sync generation
   as separate fields.
3. Show canonical memory entries and last materialization evidence together,
   while labeling workspace materialization as a derived cache.
4. If canonical retrieval fails, retain the runtime profile but render memory
   unavailable with the BFF reason; do not show a reassuring empty state.
5. If materialization fails, preserve canonical memory visibility and show the
   cache failure as a separate remediation state.

### Provider health and reauth

1. Load the provider pool overview; render auth, smoke, quota, dependencies,
   and reauth independently.
2. Disable claims such as "usable" when the live smoke is absent, stale, or
   failed, even if credentials are mounted/authenticated.
3. Start reauth only through the BFF route with operator/admin role and MFA.
4. If the response requires code entry, render the code form and submit it to
   the session-specific BFF endpoint; otherwise show provider-supplied user
   action instructions without exposing secrets.
5. Poll reauth status until succeeded, failed, or expired.
6. On success, rerun provider readiness/live smoke before changing the displayed
   usability state. Reauth success alone is not readiness proof.

## 6. Frontend State Rules

| State | UI behavior |
|---|---|
| Auth ready, smoke passed | May show usable if the BFF says usable. |
| Auth ready, smoke missing/stale | Show verification required/unknown; offer probe or refresh only if backend advertises it. |
| Auth ready, smoke failed | Show degraded with sanitized reason; offer reauth only when allowed. |
| Quota unknown | Show "quota unavailable", never `0 used` or unlimited. |
| Memory source unavailable | Show source failure, not "no memories yet". |
| Canonical memory available, materialization failed | Show memories plus a separate materialization error. |
| Reauth awaiting code | Show code-entry form; retain session ID only as opaque tracking state. |
| Reauth succeeded | Show "verifying" until readiness is rechecked. |

Frontend implementation belongs in `ajoe734/execute-plans`, not a directory
inside Pantheon. It must use live BFF mode with strict fallback for dev proof.

## 7. Suggested Parent Acceptance Tests

### BFF

- Persona memory returns Memory Plane entries with canonical IDs and source
  metadata.
- Memory Plane timeout/unreachable returns an explicit unavailable surface,
  distinguishable from an available empty collection.
- A persona cannot retrieve another persona's private memory.
- Runtime profile rejects or degrades unknown model refs without browser-side
  fallback invention.
- Provider rows keep auth, smoke, quota source, dependency completeness, and
  reauth state independent.
- Mounted/authenticated plus failed smoke is not usable.
- Reauth payloads are redacted, require role/MFA, support code-required flows,
  and require post-success readiness refresh.

### Frontend

- Codex, Claude, and OpenClaw rows render ready, degraded, unavailable, and
  unknown combinations without collapsing them.
- Code-entry appears only when the BFF advertises `awaiting_code`.
- A successful reauth transitions to verifying, not directly to usable.
- Canonical empty memory and unavailable memory have different copy.
- Materialization source IDs/timestamp are visible and workspace files are
  labeled derived cache.
- No browser request targets Memory Plane, OpenClaw adapter, or provider APIs
  directly.

## 8. Non-Claims and Parent Composition

This packet does not claim that:

- `OCLAW-PMEM-002` or `OCLAW-PMEM-003` is complete merely because related code
  exists in this checkout;
- the current persona memory endpoint already reads canonical Memory Plane;
- current provider readiness proves quota or live provider usability;
- an exact final DTO or frontend route has been canonically approved;
- frontend work has been implemented or deployed;
- OpenClaw workspace files may be edited as memory truth.

The parent owner should compose this packet with the accepted outputs of
`OCLAW-PMEM-001`, `OCLAW-PMEM-002`, and `OCLAW-PMEM-003`, then narrow endpoint
and DTO names in parent-owned tests. The sidecar reviewer should check that this
remains support-only, preserves Memory Plane authority, and never treats mount
or reauth completion as provider usability.

