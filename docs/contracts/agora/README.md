# Agora backend generation contract

`AG-COMPAT-001-BE` publishes Agora v1.13 as the deterministic frontend
generation leaf. It aggregates the implemented v1.10 performance/workshop
version routes, v1.11 workshop lifecycle routes, and v1.12 candidate truth
routes without rewriting those frozen source contracts.

## Artifacts

- `services/control-plane/openapi/agora_v1_13.openapi.yaml`: single OpenAPI 3.1
  entrypoint for the eleven revised read/write operations.
- `services/control-plane/specs/agora/bundle_index.v1_13.json`: additive parent,
  source-leaf, definition, capability-manifest, and OpenAPI hashes.
- `services/control-plane/specs/agora/v14/capability_manifest_v1_13.json`:
  combined implemented capabilities and the pending frontend evidence gate.
- `backend-generation-input.v1_13.json`: machine-readable execute-plans handoff
  bound to exact, reachable Pantheon runtime and contract commits.

`dev-compatibility-manifest.json` is not accepted by this task. It remains
pending until `AG-COMPAT-001-FE` supplies frontend runtime, generated-from,
and generated-type identities and `AG-COMPAT-002-GATE` validates the pair.

## Deterministic generation and verification

Generate or check the static bundle:

```sh
python3 docs/contracts/agora/generate_backend_contract.py bundle
python3 docs/contracts/agora/generate_backend_contract.py bundle --check
```

After committing the bundle, bind the handoff to the runtime commit containing
the backend implementations and the contract commit containing the exact
v1.13 bytes:

```sh
python3 docs/contracts/agora/generate_backend_contract.py handoff \
  --backend-runtime-commit <40-char-sha> \
  --backend-contract-commit <40-char-sha>
python3 docs/contracts/agora/generate_backend_contract.py verify
```

The verifier checks ancestry, every recorded exact-byte SHA-256, the complete
route set, external `$ref` closure, deterministic regeneration, and the
required pending compatibility state. Frontend generated output uses
`sha256-path-tab-filehash-lf-v1`: sort relative paths, write one
`<path>\t<file-sha256>\n` line per file, then SHA-256 the concatenated UTF-8
lines.
