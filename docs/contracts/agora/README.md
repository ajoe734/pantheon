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

`dev-compatibility-manifest.json` now records the accepted v1.13 pair assembled
by `AG-COMPAT-002-GATE`. It pins the backend and frontend runtime/handoff
commits, exact bundle/OpenAPI/capability/type hashes, and both `dev` branches.
The deployment workflow checks out execute-plans history and runs the
fail-closed gate before acquiring the environment lease or invoking a deploy.

## Transitive Derivation Closure And Bundle Chain

`AGORA-PROVENANCE-CLOSURE-PREREQUISITE-001` repairs complete Agora generation source
provenance across the entire 14-bundle extension chain from `bundle_index.json` (v1.0)
up through `bundle_index.v1_13.json`.

- **Transitive derivation closure**: captures all 90 distinct files (89 input/spec/bundle
  files plus the generator itself). This includes all 14 bundle indexes, intermediate
  and aggregate OpenAPI definitions, schemas, and recursive external `$ref` closures,
  notably including `services/control-plane/specs/agora/v4/research_run_projection.schema.json`
  with the `provenance` enum introduced by `AGORA-CHAIN-001`.
- **Deterministic parent-hash propagation**: deterministic generation updates the
  `extends.bundle_index_sha256` chain from `bundle_index.v1_3.json` sequentially through
  `bundle_index.v1_4.json` up to `bundle_index.v1_13.json`, along with the aggregate
  `capability_manifest_v1_13.json` `source_contracts` entries.
- **Fail-closed verification**: `verify` and `bundle --check` reject stale parent hashes,
  cyclic references, out-of-root bundle paths, missing referenced schemas/files, and
  exact-byte mismatches at the claimed contract commit.

## Deterministic generation and verification

Generate or check the static bundle artifacts (refreshes `v1_4`..`v1_13` indexes,
capability manifest, and OpenAPI):

```sh
python3 docs/contracts/agora/generate_backend_contract.py bundle
python3 docs/contracts/agora/generate_backend_contract.py bundle --check
```

Following the two-commit generation anchor protocol:
1. First, commit the verified generator, tests, and derived source metadata as
   source anchor commit `A`.
2. Second, emit and commit the backend handoff bound to the schema-containing
   runtime commit and `contract_commit = A`:

```sh
python3 docs/contracts/agora/generate_backend_contract.py handoff \
  --backend-runtime-commit <40-char-sha> \
  --backend-contract-commit <A-commit-sha>
python3 docs/contracts/agora/generate_backend_contract.py verify
```

The verifier checks ancestry, every recorded exact-byte SHA-256, the complete
route set, external `$ref` closure, deterministic regeneration, and the
required pending compatibility state. Frontend generated output uses
`sha256-path-tab-filehash-lf-v1`: sort relative paths, write one
`<path>\t<file-sha256>\n` line per file, then SHA-256 the concatenated UTF-8
lines.

Generate and verify the accepted pair from both repositories:

```sh
python3 scripts/agora_compat_manifest.py write \
  --frontend-root /path/to/execute-plans \
  --backend-runtime-commit <exact-pantheon-payload-sha> \
  --frontend-runtime-commit <exact-execute-plans-payload-sha>
python3 scripts/agora_compat_manifest.py deployment-gate \
  --manifest docs/contracts/agora/dev-compatibility-manifest.json \
  --frontend-root /path/to/execute-plans \
  --backend-runtime-commit <exact-pantheon-payload-sha> \
  --frontend-runtime-commit <exact-execute-plans-payload-sha> \
  --evidence-out /path/to/agora-compatibility-gate.json
```

`write --compatibility-status accepted` and `deployment-gate` reject
placeholder, mismatched, tampered, or non-`dev`-reachable identities.
The machine-readable handoffs remain the generated contract/type baselines.
Explicit runtime commits may advance beyond those baselines only when they are
reachable from `dev`, descend from the respective handoff runtime, and retain
the exact generated frontend type bytes. This lets the manifest bind a later
workflow/controller-only delivery commit without weakening the source
contract proof or creating a self-referential handoff commit.
The two runtime arguments bind the actual deployment payloads to the accepted
manifest instead of merely proving that older compatible commits remain in
`dev` history. With both arguments present, `--evidence-out` records the exact
manifest digest and backend/frontend Git commit/tree identities for the
frontend release controller to consume before its hosted symlink switch.
`verify --allow-pending` remains a repository-inspection path only; it is never
used by the accepting deployment workflow.
