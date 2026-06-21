# Pantheon Agora — AG-BE-SW-001 Strategy Workshop Deep Design Closure

**Date:** 2026-06-21  
**Status:** Design closure candidate; execution remains blocked until the artifacts listed in §11 are merged into `pantheon@dev`.  
**Tier:** L3 detailed design / contract-extension input  
**Scope:** Private workshop content, StrategySpec reference ownership, lifecycle status alignment, database indexes, task ownership  
**Conflict rule:** Existing AG-XR-001 v1 bundle and AG-XR-OPENAPI-001 v1.1 bundle remain immutable audit artifacts. This document requires an additive v1.2 extension; it does not silently rewrite either prior bundle.

---

## 1. Verified current-dev findings

`AG-XR-OPENAPI-001` has landed an additive `agora_v1_1.openapi.yaml` contract with the `/bff/agora/workshops` route family, ETag/`If-Match`, idempotency, research, consultation, versioning, stream and conclude semantics.

That extension is still insufficient for `AG-BE-SW-001` because:

1. `WorkshopCreateRequest.initial_message` is raw text, but the persistence design requires workshop events to persist only `private_content_ref` plus `redacted_summary`.
2. There is no canonical private-content storage contract, key policy, retention class, read policy, deletion policy or failure behavior.
3. `strategy_spec_ref` is an ambiguous string. Its mapping into the canonical Strategy Registry is not defined.
4. Workshop status values conflict between the frozen schema and the list-filter contract.
5. The database section lists only broad index shapes, not executable table/index/constraint definitions.
6. The current task assignment reportedly points to disabled `Codex2`; this is invalid even after design closure.

Therefore the worker STOP is correct.

---

## 2. Non-negotiable architecture decisions

### 2.1 Do not create a parallel StrategySpec store

Workshop persistence owns:

- session state;
- messages/events;
- completeness snapshots;
- links to Strategy Registry versions;
- idempotency and aggregate version.

Workshop persistence does **not** own or copy:

- StrategySpec JSON truth;
- StrategySpec lifecycle;
- ExperimentRun truth;
- CandidateArtifact truth.

### 2.2 Do not create a new standalone storage platform

Private content uses Pantheon's existing object-storage abstraction with:

- a dedicated private prefix/bucket;
- an Agora-specific access policy;
- envelope encryption;
- a small control-plane metadata table;
- an in-process `PrivateContentStore` interface.

A separate network service is not required for Phase 1.

### 2.3 Prior bundles remain immutable

Do not edit or re-hash in place:

- `services/control-plane/specs/agora/bundle_index.json`;
- `services/control-plane/specs/agora/bundle_index.v1_1.json`;
- `services/control-plane/openapi/agora_v1.openapi.yaml`;
- `services/control-plane/openapi/agora_v1_1.openapi.yaml`.

Create additive v1.2 artifacts.

---

## 3. Private content storage contract

### 3.1 Owner and implementation location

```text
services/control-plane/privacy/private_content_store.py
services/control-plane/privacy/private_content_models.py
services/control-plane/privacy/private_content_policy.py
```

The control-plane owns metadata and authorization. Ciphertext bytes are stored through the existing object-store adapter.

### 3.2 Interface

```python
class PrivateContentStore(Protocol):
    def put(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        workshop_id: str,
        event_id: str,
        content_type: str,
        plaintext: bytes,
        retention_class: str,
        idempotency_key: str,
    ) -> PrivateContentDescriptor: ...

    def get_for_owner(
        self,
        *,
        private_content_ref: str,
        tenant_id: str,
        owner_user_id: str,
        purpose: str,
        request_id: str,
    ) -> bytes: ...

    def delete_for_owner(
        self,
        *,
        private_content_ref: str,
        tenant_id: str,
        owner_user_id: str,
        request_id: str,
    ) -> None: ...

    def expire_due(self, *, now: datetime) -> int: ...
```

No generic list method is allowed.

### 3.3 Opaque reference

```text
private_content_ref = pcnt_<ULID>
```

The reference must not encode tenant ID, user ID, workshop ID or object-store path.

### 3.4 Encryption

Production:

```text
content encryption: AES-256-GCM
data key: one random DEK per object
key encryption: configured cloud KMS / HSM-backed KEK
AAD:
  tenant_id
  owner_user_id
  workshop_id
  event_id
  content_type
  schema_version
```

Persist only:

- encrypted DEK;
- KEK/key version;
- nonce and authentication tag as part of the envelope;
- ciphertext object URI;
- ciphertext SHA-256.

Do not persist plaintext hashes.

Development/test may use a local key provider only when:

```text
PANTHEON_ENV != production
AGORA_PRIVATE_CONTENT_DEV_KEK is injected at runtime
```

The development key must never be committed.

### 3.5 Retention classes

| Class | Default expiry | Use |
|---|---:|---|
| `workshop_default` | 90 days after creation | Normal workshop message |
| `user_saved` | 365 days, renewable by owner | Explicitly saved conversation material |
| `ephemeral_attachment` | 30 days | Temporary uploaded research material |
| `legal_hold` | No automatic expiry | Disabled by default; explicit compliance action only |

Rules:

- Default is `workshop_default`.
- Institutional learning never extends private-content retention.
- Redacted summaries and structured StrategySpec versions follow their own governed retention.
- Owner deletion is allowed unless an explicit legal hold exists.
- Expiry performs ciphertext deletion and encrypted-DEK deletion, then records a tombstone.

### 3.6 Read authorization

Raw content may be decrypted only for:

- the owning Agora user;
- the bound user-private servant session acting for that user;
- narrowly scoped break-glass compliance access with a separate audit event.

Management, institutional personas and cross-user sessions receive only `redacted_summary` and allowed structured refs.

Every decrypt must record:

```text
private_content_ref
tenant_id
owner_user_id
actor_ref
purpose
request_id
accessed_at
outcome
```

### 3.7 Logging and transport

- Never put raw content in application logs, audit payloads, traces or error envelopes.
- Owner-facing responses use TLS and `Cache-Control: no-store`.
- SSE may stream raw content only to the owner session; replay/audit logs persist references and redacted summaries, not raw chunks.
- Error messages must not echo input content.

### 3.8 Redaction requirement

Before a workshop event is committed, the BFF must produce:

```text
redacted_summary
redaction_policy_version
redaction_status = completed
```

If redaction is unavailable, fail closed:

```text
503 PRIVATE_CONTENT_REDACTION_UNAVAILABLE
```

Do not persist a message event containing only a raw-content ref without a valid redacted summary.

### 3.9 Create-message write sequence

For both workshop creation and later messages:

```text
1. authenticate and resolve tenant/user scope
2. validate size/type
3. reserve event_id and idempotency record
4. redact plaintext in memory
5. encrypt and write object; receive private_content_ref
6. transactionally create/update workshop aggregate and append event
7. commit outbox event
8. if DB transaction fails, mark ciphertext orphaned for immediate GC
9. return response; never return object-store URI
```

If object storage fails, no workshop event is created.

---

## 4. Public and internal API decisions

### 4.1 Public create request remains simple

The browser continues to send:

```json
{
  "title": "Winner branch strategy",
  "initial_message": "...raw owner text...",
  "strategy_ref": {
    "strategy_id": "optional",
    "strategy_spec_registry_id": "optional"
  }
}
```

The browser must not submit `private_content_ref`; the BFF creates it server-side. This prevents reference injection across users.

For backward compatibility, v1.2 may accept deprecated `strategy_spec_ref`, but it is interpreted only as a Strategy Registry entry ID.

### 4.2 Owner event response

Owner-facing event response may contain:

```json
{
  "event_id": "evt_...",
  "event_type": "message",
  "content": "decrypted owner-visible text",
  "content_source": "private_store",
  "redacted_summary": "...",
  "created_at": "..."
}
```

`private_content_ref` is not required in browser responses.

### 4.3 Management projection

Management receives:

```json
{
  "event_id": "evt_...",
  "event_type": "message",
  "redacted_summary": "...",
  "risk_flags": [],
  "payload_refs": [],
  "created_at": "..."
}
```

Raw `content` is forbidden.

### 4.4 Additive contract artifacts

Create:

```text
services/control-plane/specs/agora/v3/private_content_ref.schema.json
services/control-plane/specs/agora/v3/workshop_event.schema.json
services/control-plane/specs/agora/v3/workshop_storage_contract.schema.json
services/control-plane/specs/agora/v3/capability_manifest_v1_2.json
services/control-plane/openapi/agora_v1_2.openapi.yaml
services/control-plane/specs/agora/bundle_index.v1_2.json
```

`bundle_index.v1_2.json` must extend and hash the exact bytes of `bundle_index.v1_1.json`.

---

## 5. StrategySpec reference mapping

### 5.1 Canonical identities

```text
strategy_id
  Stable strategy identity across versions.

strategy_spec_registry_id
  Immutable Registry record/version ID.

active_strategy_spec_registry_id
  Workshop pointer to the currently selected Registry version.
```

### 5.2 Create from an existing strategy draft

When `strategy_ref` is supplied:

1. BFF reads the Strategy Registry record by `strategy_spec_registry_id`.
2. It verifies tenant/user scope and that `record.strategy_id` matches any supplied `strategy_id`.
3. It stores only:
   - `strategy_id`;
   - `active_strategy_spec_registry_id`.
4. It does not copy StrategySpec JSON into workshop tables.
5. Mismatch returns `409 STRATEGY_REFERENCE_MISMATCH`.
6. Missing/unauthorized record returns 404/403 without revealing cross-user existence.

### 5.3 Create from a free-form idea

If no strategy reference is supplied:

```text
strategy_id = NULL
active_strategy_spec_registry_id = NULL
```

The workshop may collect messages and completeness state.

When the first user-accepted strategy version is created:

1. call existing Strategy Registry draft-create path;
2. receive `strategy_id` and immutable `strategy_spec_registry_id`;
3. insert a workshop-version link;
4. update the workshop active pointer in one orchestrated command.

### 5.4 Workshop version semantics

A workshop version is a link to an immutable Strategy Registry version. It is not a copied StrategySpec document.

```text
workshop_version_id
workshop_id
strategy_id
strategy_spec_registry_id
parent_workshop_version_id
source_event_id
sequence_no
created_by
created_at
```

### 5.5 Existing ambiguous fields

- `strategy_spec_ref` in v1.1: deprecated alias for `strategy_spec_registry_id`.
- `selected_version_id`: response alias for the active `workshop_version_id`; do not create a second StrategySpec truth column.
- `active_strategy_spec_registry_id`: authoritative Registry pointer stored on the session.

### 5.6 Conclude semantics

Conclude requires an existing workshop-version link.

The session records:

```text
final_workshop_version_id
final_strategy_spec_registry_id
concluded_at
```

Conclude does not promote the StrategySpec lifecycle state. Registry/governance remains responsible for candidate/approved transitions.

---

## 6. Canonical status model

Canonical enum:

```text
open
in_review
concluded
archived
```

Allowed transitions:

```text
open -> in_review
in_review -> open
open -> archived
in_review -> concluded
in_review -> archived
concluded -> archived
```

Guards:

- `concluded` and `archived` reject new messages, research dispatches and version creation.
- `concluded` requires a valid final workshop version.
- `archived` is terminal.
- Reopening `in_review -> open` requires `If-Match` and an audit reason.

### 6.1 List filter

`GET /bff/agora/workshops?status=` accepts exactly:

```text
open
in_review
concluded
archived
```

Do not use `active` as a lifecycle status.

For UI grouping, add:

```text
status_group=active   => open + in_review
status_group=closed   => concluded + archived
```

Responses always emit canonical status values.

---

## 7. Persistence schema

### 7.1 `strategy_workshop_session`

```text
workshop_id                         text PK
tenant_id                           text NOT NULL
user_id                             text NOT NULL
servant_persona_id                  text NOT NULL
openclaw_session_id                 text NULL
strategy_id                         text NULL
active_strategy_spec_registry_id    text NULL
active_workshop_version_id          text NULL
final_strategy_spec_registry_id     text NULL
final_workshop_version_id           text NULL
status                              text NOT NULL
lock_version                        bigint NOT NULL DEFAULT 1
title                               text NULL
created_at                          timestamptz NOT NULL
updated_at                          timestamptz NOT NULL
concluded_at                        timestamptz NULL
archived_at                         timestamptz NULL
```

Check constraint:

```sql
CHECK (status IN ('open','in_review','concluded','archived'))
```

### 7.2 `strategy_workshop_event`

```text
event_id                    text PK
workshop_id                 text NOT NULL FK
sequence_no                 bigint NOT NULL
actor_type                  text NOT NULL
actor_ref                   text NULL
event_type                  text NOT NULL
private_content_ref         text NULL
redacted_summary            text NULL
redaction_policy_version    text NULL
payload_refs_json           jsonb NOT NULL DEFAULT '[]'
trace_id                    text NOT NULL
request_id                  text NOT NULL
created_at                  timestamptz NOT NULL
```

Constraints:

```sql
UNIQUE (workshop_id, sequence_no)

CHECK (
  event_type <> 'message'
  OR (
    private_content_ref IS NOT NULL
    AND redacted_summary IS NOT NULL
    AND redaction_policy_version IS NOT NULL
  )
)
```

### 7.3 `strategy_workshop_version_link`

```text
workshop_version_id             text PK
workshop_id                     text NOT NULL FK
strategy_id                     text NOT NULL
strategy_spec_registry_id       text NOT NULL
parent_workshop_version_id      text NULL
source_event_id                 text NULL
sequence_no                     bigint NOT NULL
created_by                      text NOT NULL
created_at                      timestamptz NOT NULL
```

Constraints:

```sql
UNIQUE (workshop_id, sequence_no)
UNIQUE (workshop_id, strategy_spec_registry_id)
```

### 7.4 `strategy_completeness_snapshot`

```text
snapshot_id                  text PK
workshop_id                  text NOT NULL FK
workshop_version_id          text NULL
assessment_version           bigint NOT NULL
state_map_json               jsonb NOT NULL
blocking_items_json          jsonb NOT NULL
next_question_json           jsonb NULL
created_at                   timestamptz NOT NULL
```

Constraint:

```sql
UNIQUE (workshop_id, assessment_version)
```

### 7.5 `agora_private_content_object`

```text
private_content_ref          text PK
tenant_id                    text NOT NULL
owner_user_id                text NOT NULL
workshop_id                  text NOT NULL
event_id                     text NULL
object_uri                   text NOT NULL
ciphertext_sha256            char(64) NOT NULL
encrypted_dek                bytea NOT NULL
kek_key_version              text NOT NULL
content_type                 text NOT NULL
retention_class              text NOT NULL
expires_at                   timestamptz NULL
state                        text NOT NULL
created_at                   timestamptz NOT NULL
deleted_at                   timestamptz NULL
```

No plaintext body or plaintext hash is stored.

---

## 8. Required indexes — closure of SD §22.6

```sql
CREATE INDEX ix_workshop_user_status_updated
ON strategy_workshop_session
(tenant_id, user_id, status, updated_at DESC);

CREATE INDEX ix_workshop_servant_status_updated
ON strategy_workshop_session
(servant_persona_id, status, updated_at DESC);

CREATE INDEX ix_workshop_strategy_updated
ON strategy_workshop_session
(strategy_id, updated_at DESC)
WHERE strategy_id IS NOT NULL;

CREATE INDEX ix_workshop_active_registry_ref
ON strategy_workshop_session
(active_strategy_spec_registry_id)
WHERE active_strategy_spec_registry_id IS NOT NULL;

CREATE UNIQUE INDEX ux_workshop_openclaw_session
ON strategy_workshop_session
(openclaw_session_id)
WHERE openclaw_session_id IS NOT NULL;

CREATE UNIQUE INDEX ux_workshop_event_sequence
ON strategy_workshop_event
(workshop_id, sequence_no);

CREATE INDEX ix_workshop_event_created
ON strategy_workshop_event
(workshop_id, created_at, sequence_no);

CREATE INDEX ix_workshop_event_trace
ON strategy_workshop_event
(trace_id);

CREATE UNIQUE INDEX ux_workshop_event_private_ref
ON strategy_workshop_event
(private_content_ref)
WHERE private_content_ref IS NOT NULL;

CREATE UNIQUE INDEX ux_workshop_version_sequence
ON strategy_workshop_version_link
(workshop_id, sequence_no);

CREATE UNIQUE INDEX ux_workshop_registry_version
ON strategy_workshop_version_link
(workshop_id, strategy_spec_registry_id);

CREATE INDEX ix_workshop_version_strategy
ON strategy_workshop_version_link
(strategy_id, created_at DESC);

CREATE UNIQUE INDEX ux_workshop_completeness_version
ON strategy_completeness_snapshot
(workshop_id, assessment_version);

CREATE INDEX ix_workshop_completeness_latest
ON strategy_completeness_snapshot
(workshop_id, created_at DESC);

CREATE UNIQUE INDEX ux_private_content_object_uri
ON agora_private_content_object
(object_uri);

CREATE INDEX ix_private_content_owner_expiry
ON agora_private_content_object
(tenant_id, owner_user_id, expires_at)
WHERE state = 'active';

CREATE INDEX ix_private_content_workshop_created
ON agora_private_content_object
(workshop_id, created_at DESC);

CREATE INDEX ix_private_content_expiry_gc
ON agora_private_content_object
(expires_at)
WHERE state = 'active' AND expires_at IS NOT NULL;
```

Use the existing BFF idempotency store with aggregate type `strategy_workshop`; do not create a duplicate idempotency subsystem unless the existing store cannot scope by tenant/user/operation/key.

---

## 9. Error contract additions

```text
PRIVATE_CONTENT_STORE_UNAVAILABLE        503
PRIVATE_CONTENT_REDACTION_UNAVAILABLE    503
PRIVATE_CONTENT_EXPIRED                  410
PRIVATE_CONTENT_ACCESS_DENIED            403
STRATEGY_REFERENCE_MISMATCH              409
STRATEGY_REFERENCE_NOT_FOUND             404
WORKSHOP_ALREADY_CONCLUDED               409
WORKSHOP_ARCHIVED                        409
WORKSHOP_VERSION_REQUIRED                409
CONCURRENT_MODIFICATION                  409
```

Errors must never include raw message content, object URI or encrypted key material.

---

## 10. Acceptance tests for AG-BE-SW-001

### Private content

- Raw `initial_message` is absent from Postgres rows, logs, traces and audit payloads.
- Ciphertext is unreadable without the DEK/KEK path.
- Event stores `private_content_ref`, valid redacted summary and policy version.
- Cross-user read returns 404/403 without existence leakage.
- Management projection never decrypts.
- Expired content returns 410 and remains represented only by a tombstone.
- DB failure after object write creates an orphan-GC record.
- Repeating the same idempotency key does not create a second object/event.

### Strategy references

- Existing Registry draft resolves to one stable `strategy_id`.
- Mismatched `strategy_id` and registry ID fails.
- Free-form workshop creates no duplicate StrategySpec.
- First accepted version creates one Registry draft and one workshop link.
- Version selection changes only pointers.
- Conclude records final refs but does not approve/promote the strategy.

### Status and concurrency

- Schema, OpenAPI filter and DB check use the same four statuses.
- `status_group=active` expands to open/in_review.
- Mutations require `If-Match` and `Idempotency-Key`.
- Stale ETag returns current version/ETag and changes nothing.
- No mutation is accepted after concluded/archived.

### Index verification

- Migration test asserts every index in §8 exists.
- Query-plan smoke covers:
  - user workshop list;
  - strategy drilldown;
  - ordered event replay;
  - latest completeness snapshot;
  - expiry GC scan.

---

## 11. Required contract artifacts before execution resumes

Create and merge:

```text
AG-DES-SW-PRIV-001
  private-content storage, encryption, retention and redaction contract

AG-DES-SW-REF-001
  Strategy Registry reference and workshop-version mapping contract

AG-DES-SW-DB-001
  workshop tables, lifecycle alignment and exact index migration

AG-XR-OPENAPI-002
  additive Agora v1.2 OpenAPI/capability/schema bundle
```

`AG-BE-SW-001` depends on all four.

---

## 12. Task ownership correction

Current reported assignment to disabled `Codex2` is invalid.

After the four design artifacts are merged:

```yaml
task: AG-BE-SW-001
owner: Claude
reviewer: Codex
status: todo
depends_on:
  - AG-DES-SW-PRIV-001
  - AG-DES-SW-REF-001
  - AG-DES-SW-DB-001
  - AG-XR-OPENAPI-002
```

Rationale:

- implementation is primarily control-plane/BFF/persistence integration;
- schema and acceptance review belong to Codex;
- a disabled worker must not remain the task owner.

Until then:

```yaml
status: blocked_design
dispatchable: false
```

---

## 13. Final dispatch decision

`AG-BE-SW-001` remains **STOP** now.

It becomes dispatchable only when:

1. v1.2 contract artifacts are merged and hashed;
2. private-content policy has an executable interface and persistence fields;
3. StrategySpec mapping is unambiguous;
4. lifecycle status is aligned;
5. exact migrations/indexes exist;
6. owner is reassigned to an enabled worker.

Operations should not invent any of these semantics.
