# AG-DES-SW-PRIV-001 Acceptance Packet and Dependency Map

**Sidecar Task ID**: `AG-DES-SW-PRIV-001-SIDECAR-ACCEPTANCE`
**Parent Task**: `AG-DES-SW-PRIV-001`
**Parent Task Title**: Private-content storage, encryption, retention and redaction contract
**Parent Owner**: (pending reassignment — was Codex2 / disabled)
**Parent Reviewer**: Claude
**Sidecar Owner**: Claude2
**Sidecar Reviewer**: Claude
**Helper Kind**: `acceptance_packet`
**Date**: 2026-06-21
**Source Design Doc**: `docs/04/pantheon_agora_cross_repo_2026-06-20/sw001-deep-closure/AG-BE-SW-001_deep_design_closure_2026-06-21.md`

This is a support artifact only. It does not update canonical truth, L1 policy,
core contracts, runtime behavior, registry logic, or compose wiring. The parent
owner decides whether and how to use this packet in the main `AG-DES-SW-PRIV-001`
finalization.

---

## 1. Scope Snapshot

`AG-DES-SW-PRIV-001` is a **design contract task** — it must produce and merge the
canonical private-content storage, encryption, retention, and redaction contract
before `AG-BE-SW-001` (Agora Workshop backend implementation) may be dispatched.

It is one of four required design artifacts listed in the deep-design closure doc:

| Design Task | Scope |
|---|---|
| `AG-DES-SW-PRIV-001` | Private-content storage, encryption, retention and redaction contract |
| `AG-DES-SW-REF-001` | Strategy Registry reference and workshop-version mapping contract |
| `AG-DES-SW-DB-001` | Workshop tables, lifecycle alignment and exact index migration |
| `AG-XR-OPENAPI-002` | Additive Agora v1.2 OpenAPI/capability/schema bundle |

`AG-BE-SW-001` depends on all four. Until all four are merged into `dev`,
`AG-BE-SW-001` is `blocked_design` / non-dispatchable.

Current task status when this packet was created:

| Task | Owner | Reviewer | Status | Note |
|---|---|---|---|---|
| `AG-DES-SW-PRIV-001` | (pending reassignment) | Claude | `todo` | Design task not yet started; previous owner Codex2 is disabled. |
| `AG-BE-SW-001` | Claude (pending) | Codex (pending) | `blocked_design` | Blocked on all four design artifacts. |

---

## 2. Contract Scope — What AG-DES-SW-PRIV-001 Must Deliver

The contract artifact for this task must specify all of the following, drawn from
§§3–3.9 of the deep design closure doc:

### 2.1 PrivateContentStore interface

The contract must define a `PrivateContentStore(Protocol)` with exactly four methods:
- `put(...)` → `PrivateContentDescriptor`
- `get_for_owner(...)` → `bytes`
- `delete_for_owner(...)` → `None`
- `expire_due(*, now)` → `int`

No generic list method is permitted.

Owner/implementation location:
```
services/control-plane/privacy/private_content_store.py
services/control-plane/privacy/private_content_models.py
services/control-plane/privacy/private_content_policy.py
```

### 2.2 Opaque reference format

The reference format must be:
```
private_content_ref = pcnt_<ULID>
```

The reference must not encode tenant ID, user ID, workshop ID, or object-store path.

### 2.3 Encryption spec

Production encryption must specify:
- AES-256-GCM content encryption
- One random DEK per object
- KEK via configured cloud KMS / HSM
- AAD covering: `tenant_id`, `owner_user_id`, `workshop_id`, `event_id`, `content_type`, `schema_version`

Persisted fields: `encrypted_dek`, `kek_key_version`, nonce+tag in envelope, ciphertext object URI, ciphertext SHA-256.

Must not persist: plaintext hashes.

Dev/test: local key provider only when `PANTHEON_ENV != production` and `AGORA_PRIVATE_CONTENT_DEV_KEK` is injected at runtime. The dev key must never be committed.

### 2.4 Retention classes

Contract must define:

| Class | Default expiry | Use |
|---|---:|---|
| `workshop_default` | 90 days | Normal workshop message |
| `user_saved` | 365 days, renewable | User-saved conversation material |
| `ephemeral_attachment` | 30 days | Temporary uploaded research material |
| `legal_hold` | No automatic expiry | Compliance action only; disabled by default |

Rules the contract must enforce:
- Default is `workshop_default`.
- Institutional learning must not extend private-content retention.
- Owner deletion is allowed unless explicit legal hold exists.
- Expiry performs ciphertext deletion, encrypted-DEK deletion, then records a tombstone.

### 2.5 Read authorization

Contract must state who may decrypt:
- The owning Agora user.
- The bound user-private servant session acting for that user.
- Narrowly scoped break-glass compliance access with a separate audit event.

Management, institutional personas, and cross-user sessions receive only `redacted_summary` and allowed structured refs.

Every decrypt must be audited with:
```
private_content_ref, tenant_id, owner_user_id, actor_ref, purpose,
request_id, accessed_at, outcome
```

### 2.6 Logging and transport rules

Contract must prohibit:
- Raw content in application logs, audit payloads, traces, or error envelopes.
- Error messages echoing input content.

Contract must require:
- TLS and `Cache-Control: no-store` for owner-facing responses.
- SSE may stream raw content only to the owner session.
- Replay/audit logs persist references and redacted summaries, not raw chunks.

### 2.7 Redaction gate

Before a workshop event is committed, the BFF must produce:
```
redacted_summary
redaction_policy_version
redaction_status = completed
```

Fail closed with `503 PRIVATE_CONTENT_REDACTION_UNAVAILABLE` if redaction is unavailable.
A message event must not be persisted containing only a raw-content ref without a valid redacted summary.

### 2.8 Create-message write sequence

Contract must document the ordered write sequence:
1. Authenticate and resolve tenant/user scope.
2. Validate size/type.
3. Reserve `event_id` and idempotency record.
4. Redact plaintext in memory.
5. Encrypt and write object; receive `private_content_ref`.
6. Transactionally create/update workshop aggregate and append event.
7. Commit outbox event.
8. If DB transaction fails, mark ciphertext orphaned for immediate GC.
9. Return response; never return object-store URI.

If object storage fails, no workshop event is created.

---

## 3. Acceptance Checklist for AG-DES-SW-PRIV-001

The owner uses this list to confirm the contract artifact is complete before handoff.

### 3.1 Interface completeness

| Check | Pass condition |
|---|---|
| `PrivateContentStore(Protocol)` defined with all four methods | Exact signatures match §3.2 of the closure doc |
| No `list` method or wildcard read | Absent from the interface |
| `PrivateContentDescriptor` model defined | Includes at least: `private_content_ref`, `workshop_id`, `event_id`, `retention_class`, `expires_at`, `state` |

### 3.2 Opaque reference

| Check | Pass condition |
|---|---|
| `pcnt_<ULID>` format documented | Yes |
| Ref does not encode tenant/user/workshop/object-path | Explicitly stated in contract |

### 3.3 Encryption

| Check | Pass condition |
|---|---|
| AES-256-GCM specified | Yes |
| One DEK per object | Yes |
| Cloud KMS / HSM KEK | Yes |
| AAD fields enumerated | All six fields present |
| Persisted fields listed | encrypted_dek, kek_key_version, nonce+tag envelope, object URI, SHA-256 |
| Plaintext hash prohibition stated | Yes |
| Dev key injection method documented | `AGORA_PRIVATE_CONTENT_DEV_KEK` env var; not committed |
| `PANTHEON_ENV != production` guard stated | Yes |

### 3.4 Retention

| Check | Pass condition |
|---|---|
| All four retention classes defined | `workshop_default`, `user_saved`, `ephemeral_attachment`, `legal_hold` |
| Expiry durations correct | 90d, 365d renewable, 30d, no-expiry respectively |
| Legal hold requires explicit compliance action | Yes |
| Institutional learning must not extend retention | Explicitly prohibited |
| Owner deletion allowed absent legal hold | Yes |
| Expiry tombstone defined | Yes |

### 3.5 Authorization

| Check | Pass condition |
|---|---|
| Owner, bound servant, break-glass listed as authorized actors | Yes |
| Management and cross-user sessions receive only redacted projection | Yes |
| Decrypt audit record fields enumerated | All eight fields present |

### 3.6 Logging / transport

| Check | Pass condition |
|---|---|
| Raw content banned from logs/traces/error envelopes | Explicitly stated |
| TLS + `Cache-Control: no-store` required | Yes |
| SSE raw stream restricted to owner session | Yes |
| Audit/replay logs store refs, not raw chunks | Yes |
| Error messages must not echo content | Yes |

### 3.7 Redaction gate

| Check | Pass condition |
|---|---|
| Required output fields: `redacted_summary`, `redaction_policy_version`, `redaction_status` | All three required |
| Fail-closed error code: `503 PRIVATE_CONTENT_REDACTION_UNAVAILABLE` | Defined |
| No message event without valid redacted summary | Explicitly prohibited |

### 3.8 Write sequence

| Check | Pass condition |
|---|---|
| Nine-step write sequence documented | All nine steps present and in order |
| Object-store failure → no event created | Stated |
| DB failure → orphan-GC record | Stated |
| Object URI never returned to client | Stated |

### 3.9 Error codes

| Check | Pass condition |
|---|---|
| `PRIVATE_CONTENT_STORE_UNAVAILABLE` (503) defined | Yes |
| `PRIVATE_CONTENT_REDACTION_UNAVAILABLE` (503) defined | Yes |
| `PRIVATE_CONTENT_EXPIRED` (410) defined | Yes |
| `PRIVATE_CONTENT_ACCESS_DENIED` (403) defined | Yes |
| Error responses must not include raw content or key material | Stated |

### 3.10 Additive contract artifacts

The contract must list these v1.2 artifacts as outputs:
```
services/control-plane/specs/agora/v3/private_content_ref.schema.json
services/control-plane/specs/agora/v3/workshop_event.schema.json
services/control-plane/specs/agora/v3/workshop_storage_contract.schema.json
```

Prior bundles (`bundle_index.json`, `bundle_index.v1_1.json`, `agora_v1.openapi.yaml`,
`agora_v1_1.openapi.yaml`) must remain immutable. New artifacts are additive.

---

## 4. Dependency Map

### 4.1 Upstream dependencies (what AG-DES-SW-PRIV-001 needs before or during design)

| Dependency | Status | Why it matters |
|---|---|---|
| `AG-XR-001` (Agora v1 bundle) | `done` (immutable) | Frozen v1 contract; provides baseline `agora_v1.openapi.yaml` that this task extends additively. |
| `AG-XR-OPENAPI-001` (Agora v1.1 bundle) | `done` (immutable) | Frozen v1.1 `agora_v1_1.openapi.yaml`; the `/bff/agora/workshops` route family and ETag/idempotency pattern this task must align with. |
| Existing control-plane object-store adapter | active | Private content encryption writes through this; the contract must use `PrivateContentStore` as an in-process adapter over this abstraction, not a new standalone service. |
| Existing BFF idempotency store (`aggregate_type=strategy_workshop`) | active | The write sequence uses this; do not create a duplicate idempotency subsystem. |

### 4.2 Peer design artifacts (parallel; must all merge before AG-BE-SW-001)

| Task | Scope | Merge order |
|---|---|---|
| `AG-DES-SW-REF-001` | Strategy Registry reference and workshop-version mapping | Parallel; no ordering constraint between PRIV-001 and REF-001. |
| `AG-DES-SW-DB-001` | Workshop tables, lifecycle alignment, exact index migration | Parallel; may proceed concurrently with PRIV-001. DB schema references `agora_private_content_object` which must align with the PRIV-001 contract. |
| `AG-XR-OPENAPI-002` | Additive Agora v1.2 OpenAPI + capability + schema bundle | Parallel; must hash the exact bytes of `bundle_index.v1_1.json` in its `bundle_index.v1_2.json`. |

### 4.3 Downstream tasks (blocked until PRIV-001 merges)

| Task | Current status | Dependency reason |
|---|---|---|
| `AG-BE-SW-001` | `blocked_design` | Requires all four design artifacts to be merged; `PrivateContentStore` interface and encryption contract are direct implementation inputs. |

### 4.4 Ownership correction note

The deep closure doc records that task ownership should be corrected:
- Previous assignment to `Codex2` is invalid (agent disabled).
- `AG-BE-SW-001` owner: reassign to `Claude` (reviewer: `Codex`) after design artifacts merge.
- `AG-DES-SW-PRIV-001` owner should be a currently-enabled agent with control-plane/privacy expertise.

---

## 5. Non-Claims

This acceptance packet does not:

| Non-claim | Correct owner |
|---|---|
| Produce or validate the actual `private_content_store.py` implementation | `AG-BE-SW-001` (implementation task) |
| Produce schema JSON artifacts (`private_content_ref.schema.json`, etc.) | `AG-DES-SW-PRIV-001` owner |
| Produce or modify the v1.2 OpenAPI bundle | `AG-XR-OPENAPI-002` |
| Define the workshop database schema or migrations | `AG-DES-SW-DB-001` |
| Define the Strategy Registry reference mapping | `AG-DES-SW-REF-001` |
| Modify L1 canonical policy or core contracts | Prohibited by sidecar scope rule |

---

## 6. Reviewer Checklist for Claude

| Check | Expected answer |
|---|---|
| Did this sidecar avoid canonical/runtime edits? | Yes. Only this support packet is created. |
| Does the packet accurately reflect the scope defined in the deep closure doc §§3–3.9 and §10? | Yes; acceptance checklist items trace directly to those sections. |
| Is the dependency map complete and consistent with §§11–12 of the deep closure doc? | Yes. Upstream, peer, and downstream tasks are identified. |
| Does this packet misstate any non-claim as a delivered artifact? | No. All non-claims are explicit. |
| Is the ownership correction note accurate? | Yes; the deep closure doc §12 states Codex2 is disabled and prescribes the corrected assignment. |

---

## 7. Handoff

**To**: Claude  
**From**: Claude2  
**Requested review outcome**: Approve this sidecar if the acceptance packet and
dependency map are accurate support material for `AG-DES-SW-PRIV-001`.

Recommended parent-owner use:

1. Use §§2–3 as the implementation-completeness checklist when writing the
   private-content contract artifact.
2. Use §4 as the dependency ordering guide before merging into `dev`.
3. Do not merge `AG-BE-SW-001` until all four design tasks in §1 are `done`.
4. Correct task ownership per §4.4 before dispatching `AG-BE-SW-001`.
5. Keep this packet as support material, not canonical contract truth.
