# Cross-Repo Compatibility Manifest and Hash Rules

## Canonical path

Both repositories store the same generated manifest at:

```text
docs/contracts/agora/dev-compatibility-manifest.json
```

Schema authority:

```text
pantheon/services/control-plane/specs/agora/v2/compatibility_manifest.schema.json
```

## Commit semantics

- `backend.runtime_commit`: exact 40-character SHA deployed for the BFF.
- `backend.contract_commit`: exact 40-character SHA that owns the referenced contract bundle.
- `frontend.runtime_commit`: exact 40-character SHA used to build the Agora frontend.
- `frontend.generated_from_contract_commit`: must equal `backend.contract_commit`.

Runtime commits may advance independently only when all recorded contract hashes remain unchanged and required capabilities are still advertised.

## Hash rules

### File hash

```text
SHA-256 of exact Git file bytes.
```

Requirements:

- UTF-8 for textual contract files
- no BOM
- LF endings enforced by `.gitattributes`
- no whitespace normalization during hash calculation

This matches the current `agora_schema_bundle.py` behavior.

### Bundle index hash

`base_bundle_index_sha256` is SHA-256 of the exact bytes of the frozen `bundle_index.json`.

`extension_bundle_index_sha256` is SHA-256 of the exact bytes of `bundle_index.v1_1.json`.

### Generated types hash

Hash the emitted type bundle tar/zip or a deterministic concatenation of generated files sorted by relative path. The chosen method is recorded as `generated_types_hash_algorithm`; v1 uses:

```text
sha256-path-tab-filehash-lf-v1
```

Algorithm:

1. Sort relative paths by Unicode code-point order.
2. Hash each file's exact bytes.
3. Build one UTF-8 line per file: `<path>\t<sha256>\n`.
4. SHA-256 the concatenated lines.

## Deployment check

Deployment fails when any of these is false:

```text
frontend.generated_from_contract_commit == backend.contract_commit
frontend.base_bundle_index_sha256 == backend.base_bundle_index_sha256
frontend.extension_bundle_index_sha256 == backend.extension_bundle_index_sha256
frontend.openapi_sha256 == backend.openapi_sha256
required capabilities are advertised with compatible versions
compatibility_status == compatible
```

## Manifest ownership

Pantheon CI generates the backend half. Execute-plans CI consumes it, generates types, fills the frontend half and validates. Neither repo may hand-edit a manifest marked `generated=true`.
