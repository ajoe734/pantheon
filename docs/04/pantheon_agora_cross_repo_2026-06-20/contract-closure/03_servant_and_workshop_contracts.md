# Servant and Strategy Workshop Contracts

## A. `AG-BE-ID-002` — private trading servant

### BFF routes

```text
GET  /bff/agora/servant
POST /bff/agora/servant/ensure
POST /bff/agora/servant/reconcile
POST /bff/agora/servant/sessions
GET  /bff/agora/servant/sessions/{session_id}
POST /bff/agora/servant/sessions/{session_id}/messages
POST /bff/agora/servant/sessions/{session_id}/terminate
GET  /bff/agora/servant/sessions/{session_id}/stream
```

`POST /servant/ensure` derives `tenant_id` and `agora_user_id` from the authenticated subject. The browser must not submit another user's ID.

Required headers:

```text
Authorization
Idempotency-Key
X-Request-Id
```

Ensure identity key:

```text
(tenant_id, agora_user_id, persona_class = agora_servant)
```

Behavior:

1. Resolve existing Persona Registry object.
2. Create only when absent.
3. Reconcile workspace/capability mapping when present.
4. Never widen the tool allowlist from client input.
5. Return one stable ServantProfile and effective capability summary.

### Internal OpenClaw adapter routes

Extend the existing adapter, not a new service:

```text
POST /api/openclaw-adapter/agents/ensure
GET  /api/openclaw-adapter/agents/{persona_id}
POST /api/openclaw-adapter/agents/{persona_id}/reconcile
```

The adapter request contains Persona Registry refs, private workspace ref and a server-computed capability snapshot. It must reject runtime-binding, broker-order and capital-binding capabilities.

### Capability

Add `agora.servant.v1` in the v1.1 capability manifest with path prefixes:

```text
/bff/agora/servant
/api/openclaw-adapter/agents
```

## B. `AG-BE-SW-001` — Strategy Workshop

### Canonical route name

Use `/bff/agora/workshops`, not a second competing `/strategy-workshops` family.

### Routes

```text
GET  /bff/agora/workshops
POST /bff/agora/workshops
GET  /bff/agora/workshops/{workshop_id}
POST /bff/agora/workshops/{workshop_id}/messages
GET  /bff/agora/workshops/{workshop_id}/events
GET  /bff/agora/workshops/{workshop_id}/completeness
GET  /bff/agora/workshops/{workshop_id}/versions
POST /bff/agora/workshops/{workshop_id}/versions
POST /bff/agora/workshops/{workshop_id}/versions/{version_id}/select
POST /bff/agora/workshops/{workshop_id}/research-runs
POST /bff/agora/workshops/{workshop_id}/consultations
POST /bff/agora/workshops/{workshop_id}/conclude
GET  /bff/agora/workshops/{workshop_id}/stream
```

### Ownership

Workshop state is not StrategySpec truth. It references the active draft in the existing Strategy Registry.

### Persistence

```text
strategy_workshop_session
  workshop_id PK
  tenant_id
  user_id
  servant_persona_id
  openclaw_session_id
  strategy_id
  active_strategy_spec_registry_id
  selected_version_id
  status
  lock_version
  created_at
  updated_at

strategy_workshop_event
  event_id PK
  workshop_id FK
  sequence_no UNIQUE(workshop_id, sequence_no)
  actor_type
  event_type
  private_content_ref
  redacted_summary
  payload_refs_json
  trace_id
  created_at

strategy_completeness_snapshot
  snapshot_id PK
  workshop_id FK
  strategy_version_id
  state_map_json
  blocking_items_json
  next_question_json
  created_at
```

### Concurrency

- Workshop aggregate GET returns ETag `W/\"workshop:{id}:v{lock_version}\"`.
- Mutating routes require `If-Match` and `Idempotency-Key`.
- Mismatch returns 409 `CONCURRENT_MODIFICATION` plus current ETag and snapshot link.

### Capability

The v1.1 manifest keeps `agora.workshop.v1` but adds `/bff/agora/workshops` to its canonical prefixes. Existing evaluation/committee paths remain supported.
