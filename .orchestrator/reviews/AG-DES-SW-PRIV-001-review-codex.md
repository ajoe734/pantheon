# AG-DES-SW-PRIV-001 Review - Codex

Status: review_approved after narrow reviewer fix
Reviewer: Codex
Owner: Claude
Reviewed commit: 24d364812fcc875825cf2c873881bc844f5390bd

## Scope Reviewed

- `services/control-plane/privacy/private_content_models.py`
- `services/control-plane/privacy/private_content_policy.py`
- `services/control-plane/privacy/private_content_store.py`
- `services/control-plane/specs/agora/v3/private_content_ref.schema.json`
- `services/control-plane/specs/agora/v3/workshop_storage_contract.schema.json`

## Finding Fixed During Review

The dev/test `_DevKeyProvider` DEK wrapping path was not invertible: immediate
`_encrypt_content` then `_decrypt_content` with the same dev KEK failed with
`cryptography.exceptions.InvalidTag`. This would make the owner decrypt path
unusable in dev/test despite the contract helper exposing encryption/decryption.

Reviewer fix:

- replaced the dev DEK wrapper with AES-256-GCM using the injected
  `AGORA_PRIVATE_CONTENT_DEV_KEK`, encoding `encrypted_dek` as
  `wrapping_nonce || wrapped_dek || tag`;
- added privacy contract tests covering protocol shape, opaque ref pattern,
  schema example validity, dev encrypt/decrypt round-trip, production guard,
  fail-closed redaction, owner-only decrypt decision, and exact §9 error codes;
- corrected the v3 schema example to match the Crockford Base32 ref pattern;
- corrected `AgendaPrivateContentObject` to `AgoraPrivateContentObject`.

## Acceptance Checks

- No generic `list` method exists on `PrivateContentStore`.
- `private_content_ref` remains `pcnt_<26 char Crockford ULID>` and encodes no
  tenant/user/workshop/object-store path.
- §9 error codes and HTTP statuses match the deep-closure contract exactly.
- Policy rejects cross-user owner decrypt decisions and fails closed when
  redaction output is missing.
- No plaintext hash field is persisted; the contract stores only ciphertext
  hash metadata.
- Frozen v1/v1.1 bundle files were not changed.

## Verification

- `PYTHONPATH=services/control-plane python3 -m pytest services/control-plane/privacy/test_private_content_contract.py -q` - 7 passed
- `python3 -m json.tool services/control-plane/specs/agora/v3/private_content_ref.schema.json >/dev/null && python3 -m json.tool services/control-plane/specs/agora/v3/workshop_storage_contract.schema.json >/dev/null && echo json_ok`
- `python3 -m py_compile services/control-plane/privacy/private_content_models.py services/control-plane/privacy/private_content_policy.py services/control-plane/privacy/private_content_store.py services/control-plane/privacy/test_private_content_contract.py`

## Verdict

Approved for owner closeout after the reviewer fix commit is included on the
task branch.
