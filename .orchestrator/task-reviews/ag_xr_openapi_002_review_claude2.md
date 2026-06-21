# Review: AG-XR-OPENAPI-002 — Additive Agora v1.2 OpenAPI / capability / schema bundle

**Reviewer:** Claude2  
**Date:** 2026-06-21  
**Task commit:** f7e0b2b990524ff1e677f0ffcf5dd38ccd96a66b  
**PR:** #1983 (merged dffa0ee5a0f310e20ab423749441ec7e032fdbdb)  
**Verdict:** APPROVED

---

## Summary

The v1.2 additive bundle is correctly implemented against the sw001 deep-closure design document (`AG-BE-SW-001_deep_design_closure_2026-06-21.md`). All required artifacts are present, all tests pass, and the frozen v1/v1.1 files are untouched.

---

## Verification steps run

```
python3 -m pytest scripts/test_agora_v1_2_bundle.py -v
# Result: 5/5 passed

sha256sum services/control-plane/specs/agora/bundle_index.v1_1.json
# Result: 5f875202966d1e373ab325b7107de8355798f1e3f55cdac2548aa74607a821ee
# Matches bundle_index.v1_2.json extends.bundle_index_sha256 exactly

git diff --exit-code HEAD~1 HEAD -- \
  services/control-plane/specs/agora/bundle_index.json \
  services/control-plane/specs/agora/bundle_index.v1_1.json \
  services/control-plane/openapi/agora_v1.openapi.yaml \
  services/control-plane/openapi/agora_v1_1.openapi.yaml
# Result: exit code 0 (frozen files untouched)

python3 scripts/agora_schema_bundle.py --verify
# Result: all OK
```

---

## Contract requirements (§4 / §6 / §9 of deep-closure doc)

| Requirement | Status |
|---|---|
| **§4.1** browser sends raw `initial_message`, no `private_content_ref` | ✅ `WorkshopCreateRequest` has `initial_message`, deprecated `strategy_spec_ref`; no `private_content_ref` field |
| **§4.1** deprecated `strategy_spec_ref` still accepted, interpreted as `strategy_spec_registry_id` | ✅ marked `deprecated: true` with correct description |
| **§4.2** owner event projection may include decrypted `content`, never returns `private_content_ref` | ✅ `OwnerWorkshopEventResponse` has `content`, `content_source`, `redacted_summary`; no `private_content_ref` |
| **§4.3** management projection: `redacted_summary` only, raw content forbidden | ✅ `ManagementWorkshopEventProjection` omits `content` and `private_content_ref` |
| **§4.4** all required v3 schema files created | ✅ All 6 required schemas present in `specs/agora/v3/` |
| **§6** canonical status enum: `open, in_review, concluded, archived` | ✅ `WorkshopStatus` enum matches exactly |
| **§6.1** `status_group=active` expands to `open + in_review`; `closed` to `concluded + archived` | ✅ List endpoint `status_group` param with description documenting expansion |
| **§6.1** authority order: v1.2 replaces v1.1 `status=active` lifecycle filter wording | ✅ Stated in OpenAPI `info.description` and capability manifest `authority_order` array |
| **§9** all 10 error codes present in `ErrorResponse.error.code` enum | ✅ All 10 codes verified in YAML and by test |
| **§9** error messages must not echo raw content, object URI, key material | ✅ `ErrorResponse.error.message` description explicitly says so; `details` description repeats the restriction |
| **Frozen v1/v1.1 immutable** | ✅ `git diff --exit-code` on all four frozen files returns 0 |
| **Bundle extends exact v1.1 bytes** | ✅ Computed SHA256 matches `bundle_index.v1_2.json` extends hash |

## Capability manifest

- `manifest_version: "1.2"` ✅
- `extends_manifest` points to v1.1 ✅
- `agora.workshop.v1` at `version: "1.2"` with `lifecycle_filters`, `private_content_contract`, `strategy_reference_contract`, `error_codes` ✅
- `authority_order` array explicitly supersedes v1.1 `status=active` wording ✅
- `v1_2_replaces_v1_1_status_active_wording: true` ✅
- raw content forbidden in `application_logs, audit_payloads, traces, error_envelopes, postgres_plaintext, management_projection` ✅

## Scope guard

- Owned layer: OpenAPI, capability manifest, bundle index, bundle tests — additive only
- Frozen v1/v1.1 artifacts: untouched ✅
- BFF implementation, persistence migrations, generated types: not touched (as declared) ✅
- No new services, no schema/route/enum invented outside the design closure ✅

---

## Verdict

All acceptance criteria are met. The additive v1.2 bundle is a faithful implementation of the sw001 deep-closure spec. No required changes.

**Approved and returned to Codex for finalization.**
