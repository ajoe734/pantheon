# AG-DES-SW-PRIV-001 Sidecar: Review Packet and Evidence Summary

| Field | Value |
|---|---|
| Task ID | `AG-DES-SW-PRIV-001-SIDECAR-REVIEW` |
| Helper kind | `review_packet` |
| Parent task | `AG-DES-SW-PRIV-001` — private-content storage, encryption, retention, and redaction contract |
| Sidecar owner / reviewer | Claude2 / Claude |
| Prepared by | Claude2 |
| Date | 2026-06-21 |
| Mutates canonical truth | false |
| Status | Ready for reviewer handoff |

## Purpose

This support-only packet assembles the review evidence and design summary for the
parent task `AG-DES-SW-PRIV-001`. The parent task must produce a merged design
artifact before `AG-BE-SW-001` can be dispatched.

This packet does not modify L1 canonical truth, OpenAPI bundles, BFF runtime
code, route registries, database schemas, or governance policy. All decisions
and artifacts remain the responsibility of the parent task owner and the
`AG-BE-SW-001` execution owner.

---

## 1. What AG-DES-SW-PRIV-001 Must Deliver

Per the deep design closure
(`docs/04/pantheon_agora_cross_repo_2026-06-20/sw001-deep-closure/AG-BE-SW-001_deep_design_closure_2026-06-21.md`,
§11), `AG-DES-SW-PRIV-001` must produce a merged contract covering:

- Private-content storage interface (`PrivateContentStore` Protocol)
- Encryption envelope (AES-256-GCM, per-object DEK, KMS-backed KEK, AAD)
- Retention class definitions and lifecycle rules
- Redaction requirement and fail-closed behavior
- Read authorization policy (owner-only decrypt, management-only redacted summary)
- Logging and transport constraints (no plaintext in logs/traces, TLS + `Cache-Control: no-store`)
- Opaque reference format (`pcnt_<ULID>`)
- Create-message write sequence (9-step atomic-safe path)

The contract must be merged into `pantheon@dev` before `AG-BE-SW-001` is
dispatched to any worker.

---

## 2. Design Decisions — Status Check

The deep design closure (§2, §3) has already specified the non-negotiable
architecture decisions. This section summarizes their current status:

### 2.1 Do Not Create a Parallel StrategySpec Store — CONFIRMED

Workshop persistence owns session state, events, completeness snapshots, and
Registry version links. It does not copy StrategySpec JSON, ExperimentRun
truth, or CandidateArtifact truth. This is confirmed in the deep design closure
§2.1 and is not open for re-interpretation.

### 2.2 Do Not Create a Standalone Storage Service — CONFIRMED

Private content uses Pantheon's existing object-storage abstraction with a
dedicated private prefix/bucket, Agora-specific access policy, envelope
encryption, a small control-plane metadata table, and an in-process
`PrivateContentStore` interface. A separate network service is deferred.

### 2.3 Prior Bundles Remain Immutable — CONFIRMED

`agora_v1.openapi.yaml`, `agora_v1_1.openapi.yaml`, `bundle_index.json`, and
`bundle_index.v1_1.json` are frozen. `AG-DES-SW-PRIV-001` must produce additive
v1.2 artifacts, not edits to frozen bundles.

---

## 3. Required Artifacts

The parent task must produce the following files. None of these exist yet in
`pantheon@dev`.

| Artifact | Location | Status |
|---|---|---|
| `PrivateContentStore` Protocol | `services/control-plane/privacy/private_content_store.py` | NOT CREATED |
| Private content domain models | `services/control-plane/privacy/private_content_models.py` | NOT CREATED |
| Private content policy | `services/control-plane/privacy/private_content_policy.py` | NOT CREATED |
| `private_content_ref` JSON schema | `services/control-plane/specs/agora/v3/private_content_ref.schema.json` | NOT CREATED |
| Workshop event schema v3 | `services/control-plane/specs/agora/v3/workshop_event.schema.json` | NOT CREATED |
| Workshop storage contract schema | `services/control-plane/specs/agora/v3/workshop_storage_contract.schema.json` | NOT CREATED |
| Capability manifest v1.2 | `services/control-plane/specs/agora/v3/capability_manifest_v1_2.json` | NOT CREATED |
| Agora OpenAPI v1.2 | `services/control-plane/openapi/agora_v1_2.openapi.yaml` | NOT CREATED |
| Bundle index v1.2 | `services/control-plane/specs/agora/bundle_index.v1_2.json` | NOT CREATED |

`bundle_index.v1_2.json` must hash the exact bytes of `bundle_index.v1_1.json`
as its chain root.

---

## 4. Interface Specification Summary

The `PrivateContentStore` Protocol (from deep design closure §3.2):

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

Key constraints:
- No generic list method is allowed.
- `private_content_ref` format: `pcnt_<ULID>` — must not encode tenant, user, workshop, or object-store path.
- Management, institutional personas, and cross-user sessions may never call `get_for_owner` for raw decryption.

---

## 5. Encryption Envelope Summary

```
Production:
  content encryption: AES-256-GCM
  data key: one random DEK per object
  key encryption: configured cloud KMS / HSM-backed KEK
  AAD: tenant_id, owner_user_id, workshop_id, event_id, content_type, schema_version

Persist only:
  encrypted DEK
  KEK/key version
  nonce + authentication tag (part of envelope)
  ciphertext object URI
  ciphertext SHA-256

Never persist:
  plaintext body
  plaintext hash
```

Development/test allowed only when `PANTHEON_ENV != production` and
`AGORA_PRIVATE_CONTENT_DEV_KEK` is injected at runtime. The development key
must never be committed.

---

## 6. Retention Classes

| Class | Default expiry | Use |
|---|---:|---|
| `workshop_default` | 90 days after creation | Normal workshop message |
| `user_saved` | 365 days, renewable by owner | Explicitly saved conversation material |
| `ephemeral_attachment` | 30 days | Temporary uploaded research material |
| `legal_hold` | No automatic expiry | Disabled by default; explicit compliance action only |

Rules:
- Default is `workshop_default`.
- Institutional learning never extends private-content retention.
- Owner deletion is allowed unless an explicit legal hold exists.
- Expiry deletes ciphertext and encrypted DEK, then records a tombstone.

---

## 7. Read Authorization Policy

Raw content may only be decrypted for:
1. The owning Agora user.
2. The bound user-private servant session acting for that user.
3. Narrowly scoped break-glass compliance access (requires a separate audit event).

Management, institutional personas, and cross-user sessions receive only
`redacted_summary` and allowed structured refs.

Every decrypt must record: `private_content_ref`, `tenant_id`, `owner_user_id`,
`actor_ref`, `purpose`, `request_id`, `accessed_at`, `outcome`.

---

## 8. Persistence Layer

The `agora_private_content_object` control-plane table (from deep design
closure §7.5):

```sql
private_content_ref     text PK
tenant_id               text NOT NULL
owner_user_id           text NOT NULL
workshop_id             text NOT NULL
event_id                text NULL
object_uri              text NOT NULL
ciphertext_sha256       char(64) NOT NULL
encrypted_dek           bytea NOT NULL
kek_key_version         text NOT NULL
content_type            text NOT NULL
retention_class         text NOT NULL
expires_at              timestamptz NULL
state                   text NOT NULL
created_at              timestamptz NOT NULL
deleted_at              timestamptz NULL
```

Required indexes (from §8):
- `ux_private_content_object_uri` — unique on `object_uri`
- `ix_private_content_owner_expiry` — `(tenant_id, owner_user_id, expires_at)` WHERE `state = 'active'`
- `ix_private_content_workshop_created` — `(workshop_id, created_at DESC)`
- `ix_private_content_expiry_gc` — `(expires_at)` WHERE `state = 'active' AND expires_at IS NOT NULL`

The `strategy_workshop_event` table enforces the constraint that every `event_type = 'message'`
row must have non-null `private_content_ref`, `redacted_summary`, and
`redaction_policy_version`. This constraint is a DB-level guard against
silent redaction bypass.

---

## 9. Redaction Requirement

Before a workshop event is committed, the BFF must produce:
- `redacted_summary`
- `redaction_policy_version`
- `redaction_status = completed`

If redaction is unavailable, the BFF must return `503 PRIVATE_CONTENT_REDACTION_UNAVAILABLE`.
It must never persist a message event containing only a raw-content ref without a valid redacted summary.

---

## 10. Create-Message Write Sequence

The atomic-safe write path (from deep design closure §3.9):

```
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

If object storage fails, no workshop event is created. The DB transaction at
step 6 is the commit boundary. The client never receives the object-store URI.

---

## 11. Error Contract Additions

These error codes must be part of the v1.2 contract:

| Code | HTTP | Trigger |
|---|---|---|
| `PRIVATE_CONTENT_STORE_UNAVAILABLE` | 503 | Object store unreachable |
| `PRIVATE_CONTENT_REDACTION_UNAVAILABLE` | 503 | Redaction service unreachable |
| `PRIVATE_CONTENT_EXPIRED` | 410 | Content tombstoned; no longer decryptable |
| `PRIVATE_CONTENT_ACCESS_DENIED` | 403 | Caller is not the owner or authorized accessor |
| `STRATEGY_REFERENCE_MISMATCH` | 409 | Supplied `strategy_id` does not match registry record |
| `STRATEGY_REFERENCE_NOT_FOUND` | 404 | Registry record not found or unauthorized |
| `WORKSHOP_ALREADY_CONCLUDED` | 409 | Mutation attempted on concluded workshop |
| `WORKSHOP_ARCHIVED` | 409 | Mutation attempted on archived workshop |
| `WORKSHOP_VERSION_REQUIRED` | 409 | Conclude attempted with no workshop-version link |
| `CONCURRENT_MODIFICATION` | 409 | Stale `If-Match` ETag |

Errors must never include raw message content, object URI, or encrypted key material.

---

## 12. Acceptance Tests for the Private-Content Contract

The parent task must produce passing tests covering (from deep design closure §10):

| Test | Requirement |
|---|---|
| No plaintext in Postgres | Raw `initial_message` absent from all rows, logs, traces, audit payloads |
| Ciphertext unreadable | Cannot decrypt without the DEK/KEK path |
| Event constraint | Every message event has `private_content_ref`, valid redacted summary, policy version |
| Cross-user isolation | Cross-user read returns 404/403 without leaking existence |
| Management projection | Management projection never calls decrypt path |
| Expiry tombstone | Expired content returns 410; tombstone present |
| Orphan GC path | DB failure after object write creates orphan-GC record |
| Idempotency | Same idempotency key does not create a second object/event |

---

## 13. Open Questions for Reviewer

The following points are not fully resolved by the deep design closure and
require reviewer input before the parent task owner begins implementation:

1. **Orphan GC mechanism** — the closure specifies "mark ciphertext orphaned
   for immediate GC" but does not define the GC worker trigger (poll interval,
   event-driven, or async retry). The parent task owner needs to pick one and
   document it in the contract.

2. **Break-glass audit trail** — the closure requires a "separate audit event"
   for compliance break-glass access, but does not specify where this audit
   event is written (existing telemetry store, a separate compliance log, or
   the outbox). The parent task owner must designate the target.

3. **Dev KEK rotation** — the closure states the dev key must never be
   committed but does not specify how it is rotated or revoked in CI. The
   parent task owner should add a CI secret note or inject-script reference
   in the contract artifact.

4. **`PrivateContentDescriptor` shape** — the `put()` return type is named but
   not fully specified in the deep design closure. The parent task owner must
   define its fields (at minimum: `private_content_ref`, `retention_class`,
   `expires_at`, and the opaque `object_uri` for internal GC use only — never
   returned to clients).

---

## 14. Boundary and Handoff Notes

**This sidecar does not:**
- Create or modify any Python source files under `services/`.
- Create or modify any JSON schema or OpenAPI files.
- Create or modify any migration SQL.
- Touch `bundle_index.json`, `bundle_index.v1_1.json`, or any frozen bundle.
- Modify `ai-status.json` fields beyond the task lifecycle commands.

**The reviewer (Claude) should:**
- Confirm the design decisions in §2 are consistent with L1 policy (`DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md`, `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`).
- Confirm the encryption approach (AES-256-GCM, KMS KEK) is consistent with
  any existing Pantheon crypto policy.
- Flag whether any of the open questions in §13 are blockers for dispatch, or
  whether they can be resolved by the parent task owner during implementation.
- If the packet is approved, move the parent task `AG-DES-SW-PRIV-001` to
  `review_approved` through the standard `approve` command so the parent task
  owner can proceed to implementation.

---

## 16. Reviewer Decision (Claude, 2026-06-21)

**Decision: APPROVED**

The packet is accurate, complete, and internally consistent. All sections were
verified against `AG-BE-SW-001_deep_design_closure_2026-06-21.md`. No
contradictions with L1 policy (`DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md`,
`LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`) were found.

### Verified items

- §2 design decisions: consistent with L1 policy and the deep design closure;
  the "no separate network service" and "no parallel StrategySpec store" calls
  are sound.
- §4–5 encryption: AES-256-GCM + per-object DEK + KMS/HSM KEK is appropriate;
  AAD binding is correct; dev-only key path is properly guarded.
- §6 retention classes: four classes match the closure; "institutional learning
  never extends retention" is correctly enforced.
- §7 read authorization: owner-only decrypt + break-glass path + audit record
  requirement is correct.
- §8 persistence schema and indexes: match the closure §7.5 and §8 exactly.
- §9 redaction requirement: fail-closed 503 is correct.
- §10 write sequence: 9-step path matches closure §3.9.
- §11 error contract: all 10 codes verified against the closure.
- §12 acceptance tests: 8 test requirements are sufficient for dispatch.

### Open question resolutions (§13)

The four open questions are **not dispatch blockers**. Recommended resolutions
for the parent task owner to document in the contract artifact:

1. **Orphan GC mechanism** — Use an async retry queue: after step 8 marks the
   object orphaned, an outbox event triggers a background GC worker. Poll
   interval is operationally configured (suggested default: 1 minute). This is
   consistent with the existing outbox pattern used elsewhere in the platform.

2. **Break-glass audit trail target** — Route break-glass access events through
   the existing telemetry outbox (consistent with
   `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`, which routes all audit-path
   events through the outbox). The parent task owner documents this in
   `private_content_policy.py`.

3. **Dev KEK CI rotation** — Document in the contract artifact that
   `AGORA_PRIVATE_CONTENT_DEV_KEK` is injected as a CI secret (e.g. GitHub
   Actions secret / K8s sealed secret) and rotated on the same quarterly
   schedule as other platform secrets. No code change required.

4. **`PrivateContentDescriptor` shape** — The minimal shape proposed in the
   packet is correct: `private_content_ref`, `retention_class`, `expires_at`,
   and `object_uri` (internal GC only, never returned to clients). The parent
   task owner formalizes this in `private_content_models.py`.

### Review scope note

This review covers the support artifact only. The parent task `AG-DES-SW-PRIV-001`
must still produce all implementation files listed in §3 before `AG-BE-SW-001`
can be dispatched.

---

## 15. Evidence References

| Evidence | Location |
|---|---|
| Deep design closure (primary source) | `docs/04/pantheon_agora_cross_repo_2026-06-20/sw001-deep-closure/AG-BE-SW-001_deep_design_closure_2026-06-21.md` |
| Institutional learning privacy model | `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/B2_institutional_learning_privacy_model.md` |
| Dispatch unblock matrix v2 | `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/07_dispatch_unblock_matrix_v2.md` |
| Agora v1.1 OpenAPI (predecessor bundle) | `services/control-plane/openapi/agora_v1_1.openapi.yaml` |
| Agora bundle index v1.1 (chain root for v1.2) | `services/control-plane/specs/agora/bundle_index.v1_1.json` |
| BFF handoff sidecar for AG-BE-SW-001 | `support/sidecars/AG-BE-SW-001/AG-BE-SW-001-SIDECAR-BFF-HANDOFF.md` |
