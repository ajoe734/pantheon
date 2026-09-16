# AG-GAP-009 — real PrivateContentStore

## Delivered boundary

Strategy Workshop message writes now hand raw text to the control-plane-owned
`PrivateContentStore` and persist only an opaque `pcnt_<ULID>` reference plus
the fixed, non-content-bearing summary `Private workshop message` in workshop
events. The former `priv-content-stub://` references are removed.

The concrete dev/test store uses one AES-256-GCM DEK per object, wraps that DEK
with an injected or ephemeral non-production KEK, applies tenant/owner/workshop/
event/content-type AAD, and retains ciphertext only inside the private-content
layer. It provides owner-scoped get/delete, idempotent put, expiry, decrypt
audit records, and deliberately has no list operation. Production rejects the
ephemeral key provider and must inject its KMS-backed implementation.

Private-content idempotency is bound to the complete logical write identity
(tenant, owner, workshop, event, and key), with a payload fingerprint that
rejects same-identity key reuse for changed content. If the workshop event CAS
fails after encryption, the store hard-deletes the unreferenced ciphertext and
DEK and removes its idempotency record before returning the conflict.

## Acceptance evidence

- Private refs match `^pcnt_[0-9A-HJKMNP-TV-Z]{26}$`.
- Cross-owner decrypt is denied and audited.
- Repeated owner-scoped idempotent writes return the same descriptor.
- Reusing a key across workshops does not alias descriptors, while changed
  payload under the same logical identity is rejected.
- A stale workshop CAS leaves no retrievable private-content orphan.
- Workshop event payloads do not contain submitted raw text.

Focused validation:

```text
pytest -q services/control-plane/privacy/test_private_content_contract.py
# 10 passed
pytest -q services/control-plane/bff/tests/test_agora_strategy_workshop.py
# 69 passed
```

This task does not add a standalone storage service, change Strategy Registry
ownership, or provision production KMS/object-storage infrastructure.

## Dev persistence correction, 2026-09-13

The earlier concrete store was process-local, so the delivered event pointer
did not prove that a message survived restart. Postgres-backed Workshop now
selects `PostgresPrivateContentStore` in the same database and schema. Its
existing `agora_private_content_object` metadata table gains ciphertext,
nonce, and idempotency columns; body and metadata commit together, without a
new object-storage service. Memory remains available for injected test stores.

The existing `AGORA_PRIVATE_CONTENT_DEV_KEK` format is supplied by the dev
Actions secret `DEV_AGORA_PRIVATE_CONTENT_DEV_KEK` through the existing deploy
environment transport, only to operator-bff. It is not a login credential,
MFA issuer, deployment grant, or build argument. Missing key configuration
does not prevent BFF startup; private-message operations return an unavailable
response instead of accepting ephemeral content. Never rotate or discard this
key while retained private content still requires it. Production KMS remains
outside this dev change.

Both message-triggered and explicit reconstruction read private pointers via
the existing owner-scoped `get_for_owner` interface. They do not use the
redaction placeholder as strategy input. Old placeholder-based cards are
recomputed once; new same-sequence cards replay without decrypting or drafting
again. Missing old ephemeral bodies remain unavailable: this change cannot
recover text that the prior process already lost.

Focused real-Postgres tests cover a separate process reading the encrypted
body, durable Workshop reconstruction and replay, concurrent idempotent
writes, cross-tenant/owner denial, changed-payload conflicts, deletion,
expiry, failed-event compensation, wrong keys and ciphertext tampering.
No plaintext fingerprint is persisted. A missing test DSN is an explicit
skip, never persistence evidence.

This correction does not certify strategy semantics or numeric research,
produce a readiness snapshot, or claim a completed Workshop-to-paper journey.
