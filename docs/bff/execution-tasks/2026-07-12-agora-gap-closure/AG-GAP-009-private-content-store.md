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
