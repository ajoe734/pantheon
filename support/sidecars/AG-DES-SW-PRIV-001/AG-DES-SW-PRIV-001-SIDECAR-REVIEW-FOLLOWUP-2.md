# AG-DES-SW-PRIV-001 Sidecar Follow-up Review Packet

| Field | Value |
|---|---|
| Task ID | `AG-DES-SW-PRIV-001-SIDECAR-REVIEW-FOLLOWUP-2` |
| Helper kind | `review_packet` |
| Parent task | `AG-DES-SW-PRIV-001` |
| Parent title | Agora private-content storage/encryption/retention/redaction contract |
| Sidecar owner / reviewer | Codex2 / Claude |
| Date | 2026-06-21 |
| Mutates canonical truth | false |
| Status | Reviewer approved; owner closeout refresh |

This is a support-only follow-up packet. It was reviewer-approved by Claude and
this closeout refresh updates the merge facts after both the sidecar packet PR
and the parent task PR landed. It does not modify L1 canonical truth, runtime
code, registry/governance implementation, OpenAPI bundles, database migrations,
or the parent task branch.

---

## 1. Current State Snapshot

| Surface | State observed | Evidence |
|---|---|---|
| Parent PR | Merged to `dev` via PR #1979 at `539e41841e6d9fab11dd18e00aa7bcd8e3da14ba` | `gh pr view 1979` reports `MERGED` at 2026-06-21T04:28:48Z with required checks successful. |
| Parent files on `origin/dev` | `services/control-plane/privacy/` and the two private-content v3 schemas are present | `git ls-tree -r --name-only origin/dev ...` lists the privacy package and schema files. |
| This sidecar PR | Merged to `dev` via PR #1981 at `548cdaf55b68025977823c20ed71eae2d5a694f1` | `gh pr view task/AG-DES-SW-PRIV-001-SIDECAR-REVIEW-FOLLOWUP-2` reports `MERGED` at 2026-06-21T04:26:59Z with required checks successful. |
| Existing sidecars | Acceptance, review, and follow-up support packets are support-only records | `AG-DES-SW-PRIV-001-SIDECAR-ACCEPTANCE.md`, `AG-DES-SW-PRIV-001-SIDECAR-REVIEW.md`, and this file. |
| Status record caveat | The checked-out `ai-status.json` and `origin/dev:ai-status.json` do not contain this sidecar task id | `jq '.tasks[] | select(.id=="AG-DES-SW-PRIV-001-SIDECAR-REVIEW-FOLLOWUP-2")'` returned no object. |
| This closeout refresh | Support packet and task brief only | No canonical/runtime files are in scope. |

Important distinction: the earlier reviewer-handoff snapshot was taken before
the parent PR merge. As of this owner closeout refresh, the parent
private-content contract files are on `origin/dev`; the parent PR merge, not
this sidecar, is the delivery event for those files.

---

## 2. Parent Branch / Dev Evidence Summary

`git show --stat --oneline 539e4184` shows the parent PR merge added nine files
and 1291 insertions:

| File | Purpose |
|---|---|
| `.orchestrator/reviews/AG-DES-SW-PRIV-001-review-codex.md` | Reviewer decision and verification record. |
| `.orchestrator/task-briefs/ag_des_sw_priv_001.md` | Task-scoped brief generated for the parent task. |
| `services/control-plane/privacy/__init__.py` | Privacy package marker. |
| `services/control-plane/privacy/private_content_models.py` | Domain models, retention classes, audit record, envelope metadata, error codes. |
| `services/control-plane/privacy/private_content_policy.py` | Retention, read authorization, redaction, logging guard, and owner-delete policy helpers. |
| `services/control-plane/privacy/private_content_store.py` | `PrivateContentStore` protocol, key-provider abstraction, dev key provider, AES-256-GCM helpers, opaque ref generation, expiry calculation. |
| `services/control-plane/privacy/test_private_content_contract.py` | Focused privacy contract tests. |
| `services/control-plane/specs/agora/v3/private_content_ref.schema.json` | `pcnt_<ULID>` opaque ref schema. |
| `services/control-plane/specs/agora/v3/workshop_storage_contract.schema.json` | Storage, redaction, encryption envelope, audit, write-sequence, DB row, and error-code contract schema. |

No frozen v1 or v1.1 bundle files appear in the parent merge diff. The parent
merge does not modify `agora_v1.openapi.yaml`, `agora_v1_1.openapi.yaml`,
`bundle_index.json`, or `bundle_index.v1_1.json`.

---

## 3. Acceptance Coverage Map

| Requirement from `AG-DES-SW-PRIV-001` | Evidence on parent branch |
|---|---|
| `PrivateContentStore` exposes only `put`, `get_for_owner`, `delete_for_owner`, `expire_due` | `private_content_store.py`; test `test_private_content_store_protocol_has_no_list_method`. |
| No generic list or wildcard read path | Same protocol test asserts no `list` method. |
| Opaque `pcnt_<ULID>` ref that encodes no tenant/user/workshop/path | `generate_private_content_ref()` and `private_content_ref.schema.json`; test validates pattern. |
| AES-256-GCM with one random DEK per object and KEK wrapping | `_encrypt_content()`, `_decrypt_content()`, `KeyProvider`, `_DevKeyProvider`. |
| Dev key only via `AGORA_PRIVATE_CONTENT_DEV_KEK` and never in production | `_DevKeyProvider` refuses `PANTHEON_ENV=production`; test covers production guard. |
| No plaintext hash persisted | `_EncryptedEnvelope` and `workshop_storage_contract.schema.json` store `ciphertext_sha256` only. |
| Retention classes: `workshop_default`, `user_saved`, `ephemeral_attachment`, `legal_hold` | `RetentionClass`, `RETENTION_DAYS`, `validate_retention_class()`, schema enum. |
| Owner-only decrypt plus bound servant and break-glass decisions | `authorise_decrypt()` and `ReadAuthorisation` in `private_content_policy.py`. |
| Every decrypt must audit the required fields | `DecryptAuditRecord` model and schema definition. |
| Fail-closed redaction with `PRIVATE_CONTENT_REDACTION_UNAVAILABLE` | `RedactionResult`, `validate_redaction_result()`, tests for missing result. |
| Error codes from deep closure section 9 | Error classes in `private_content_models.py`; test asserts exact code/status map. |
| Create-message write sequence | `workshop_storage_contract.schema.json` `WriteSequenceStep` enum. |
| DB row shape excludes plaintext body/hash | `AgoraPrivateContentObject` schema uses encrypted DEK, ciphertext hash, internal object URI. |

The parent artifact is a design/contract surface plus testable helper logic. It
does not claim to be the production object-store/KMS implementation; production
KMS provisioning remains an ops dependency called out in the parent code.

---

## 4. Reviewer Fix Already Applied

Codex reviewed parent commit `24d36481` and fixed one concrete issue in
`ad583335`; the merged parent PR includes this reviewer fix:

- The dev/test DEK wrapping path was not invertible; `_encrypt_content()` then
  `_decrypt_content()` failed with `cryptography.exceptions.InvalidTag`.
- The fix changed `_DevKeyProvider` to wrap DEKs with AES-256-GCM using the
  injected dev KEK and to encode `encrypted_dek` as
  `wrapping_nonce || wrapped_dek || tag`.
- The fix added focused tests for protocol shape, opaque ref pattern, schema
  example validity, dev encrypt/decrypt round trip, production guard,
  fail-closed redaction, owner-only decrypt decision, and exact section 9 error
  codes.
- The fix corrected the v3 ref schema example and the storage schema object
  name `AgoraPrivateContentObject`.

Reviewer verdict in `.orchestrator/reviews/AG-DES-SW-PRIV-001-review-codex.md`:
approved for owner closeout after the reviewer fix commit is included on the
task branch.

---

## 5. Verification Record

The original sidecar handoff verified the parent branch by read-only git
inspection because `origin/dev` did not yet contain the parent privacy files.
At this owner closeout refresh, both relevant PRs have merged, so the current
verification focus is merge truth and sidecar boundary truth.

Parent reviewer recorded these commands as passing on the parent branch:

```bash
PYTHONPATH=services/control-plane python3 -m pytest services/control-plane/privacy/test_private_content_contract.py -q
python3 -m json.tool services/control-plane/specs/agora/v3/private_content_ref.schema.json >/dev/null
python3 -m json.tool services/control-plane/specs/agora/v3/workshop_storage_contract.schema.json >/dev/null
python3 -m py_compile services/control-plane/privacy/private_content_models.py services/control-plane/privacy/private_content_policy.py services/control-plane/privacy/private_content_store.py services/control-plane/privacy/test_private_content_contract.py
```

Reviewer-recorded result: 7 privacy contract tests passed; both v3 schemas
parsed; the privacy Python files compiled.

This sidecar also checked during the original handoff:

```bash
git diff --name-status origin/dev...origin/task/AG-DES-SW-PRIV-001
git diff --name-only origin/dev...origin/task/AG-DES-SW-PRIV-001 | rg 'bundle_index|agora_v1|openapi'
```

The first command shows only the nine parent task files listed in section 2.
The second command returned no matches, supporting the frozen-bundle non-change
claim.

Owner closeout refresh checked:

```bash
gh pr view 1979 --json number,state,mergedAt,mergeCommit,url,headRefName,baseRefName,statusCheckRollup
gh pr view task/AG-DES-SW-PRIV-001-SIDECAR-REVIEW-FOLLOWUP-2 --json number,state,mergedAt,mergeCommit,url,headRefName,baseRefName,isDraft,autoMergeRequest,reviewDecision,statusCheckRollup
git show --stat --oneline --decorate 539e4184
git diff --name-status 548cdaf5..539e4184
git ls-tree -r --name-only origin/dev services/control-plane/privacy services/control-plane/specs/agora/v3/private_content_ref.schema.json services/control-plane/specs/agora/v3/workshop_storage_contract.schema.json
git show origin/dev:services/control-plane/specs/agora/v3/private_content_ref.schema.json | python3 -m json.tool >/dev/null
git show origin/dev:services/control-plane/specs/agora/v3/workshop_storage_contract.schema.json | python3 -m json.tool >/dev/null
git show origin/dev:ai-status.json | jq '.tasks[] | select(.id=="AG-DES-SW-PRIV-001-SIDECAR-REVIEW-FOLLOWUP-2")'
```

Results: PR #1979 and PR #1981 are merged with required checks successful;
`origin/dev` contains the parent privacy files; the parent merge diff is the
nine-file contract surface listed above; both private-content schema blobs on
`origin/dev` parse as JSON; and this sidecar task id is absent from the
checked-out status file and from `origin/dev:ai-status.json`.

---

## 6. Handoff to Claude

Claude approved this sidecar for owner closeout. The closeout responsibility is
now limited to making this support artifact durable and attempting the normal
`done` status transition after the closeout PR merges.

Recommended parent closeout use:

1. Treat parent PR #1979 merge commit `539e4184` as the event that brought
   `services/control-plane/privacy/` and the two private-content v3 schemas into
   `dev`.
2. Keep `AG-BE-SW-001` gated on all required SW-001 design artifacts being
   merged, not merely review-approved.
3. Treat production KMS provisioning, real object-store persistence, and runtime
   BFF wiring as downstream implementation/ops work, not as delivered by this
   design sidecar.
4. Do not use this sidecar by itself as evidence that canonical runtime behavior
   changed; it is only a review packet and evidence summary.

---

## 7. Non-Claims

This sidecar does not:

- approve or merge `AG-DES-SW-PRIV-001`; that happened separately via PR #1979;
- change parent task status;
- modify parent branch files;
- modify frozen v1/v1.1 OpenAPI or bundle indexes;
- create production private-content persistence or KMS provisioning;
- unblock `AG-BE-SW-001` by itself.
