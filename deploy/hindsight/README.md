# Internal rebuildable memory index (H1/H2)

Hindsight is a projection behind the canonical memory service. Canonical
institutional facts and persona interpretations remain owner-written in the
existing stores. The memory facade and bank authorisation/mapping are delivered
by `MEMORY-HINDSIGHT-INDEX-20261113`; this slice supplies its URL and key through
`PANTHEON_MEMORY_HINDSIGHT_URL` and `PANTHEON_MEMORY_HINDSIGHT_API_KEY`. Agents and
execution services receive neither network access nor credentials. No host port
or new product route is added.

`memory-index` is an internal Docker network with exactly memory and Hindsight;
`memory-index-storage` connects only Hindsight and its dedicated Postgres. Main
Postgres and retrieval-postgres keep their existing definitions and volumes.
Hindsight uses the API-key tenant extension, disables MCP, and runs migrations
against pgroonga + pgvector at startup. Its shared API key grants all-bank access:
only the memory facade may authorise callers and hydrate canonical records.

## Pinned runtime and models

* Base: official `hindsight-api:1.2.1-slim`, OCI index digest in the Dockerfile.
  Upstream full and slim omit `local-onnx`; we extend slim with the ONNX runtime
  and tokenizer versions from upstream v1.2.1's `uv.lock`, without torch.
* Postgres: `groonga/pgroonga:4.0.8-debian-17` by digest and
  `postgresql-17-pgvector=0.8.6-1.pgdg13+1`. The official signed PGDG archive
  supplies the older package after it left the live index. Init creates both
  `vector` and `pgroonga` in the separate index database.
  The Dockerfile explicitly invokes the upstream `docker-entrypoint.sh` with
  Bash (its existing shebang) and keeps `postgres` as the default command, so
  Stage0 can check the actual script without changing native initialisation,
  argument forwarding, privilege drop or signal handling.
* Embeddings: exactly `services/search/model-manifest.json`, including external
  ONNX weights and all tokenizer files. Mean pooling, L2 normalisation, 1024
  dimensions and the `query: ` / `passage: ` prefixes match search. The same
  manifest is consumed directly rather than maintaining a second checksum list.
* Reranker: multilingual `BAAI/bge-reranker-v2-m3`, Apache-2.0, community ONNX
  int8 export; the immutable revision and SHA256 for every packaged file are in
  `model-manifest.json`. Quantisation is an implementation choice, not a model
  quality acceptance claim.

Models are downloaded and checked only during image build, then made read-only.
Every start checks every file before launching the upstream entry point. Missing
or corrupt files prevent the process (and therefore readiness) from starting.
Both HF offline flags are set and neither service has external network access.
The build requires internet access and several GB of free disk for model/image
layers; consult the measured sizes in the task evidence before deployment.

LLM settings are deliberately unselected in `.env.example`. Compose uses the
upstream `none` provider, `chunks` retain, no automatic consolidation, and no
background worker/mental-model refresh. Synchronous retain and recall work;
reflect fails closed with upstream's recorded `400` reason. Upstream `none`
validation also disables observations. The downstream facade owns the
`llm_layers: disabled` response, not this deployment slice. Per-operation
provider/model/key fields are configuration only: enabling LLM features requires
an explicit operator choice plus a reviewed egress and worker configuration.
Do not supply placeholder LLM credentials or enable async retain with workers off.

For hosted use, supply independent `MEMORY_INDEX_POSTGRES_PASSWORD` and
`HINDSIGHT_API_KEY` values through the existing private deploy environment (or
host `.env`). The example fallbacks are local dev values, as with
retrieval-postgres. Optional environment overrides are forwarded by
`deploy_nonprod_vm.sh` only when explicitly present, preserving host `.env`.

## Build, verify and release

```bash
docker compose --profile root build memory-index-postgres hindsight
docker compose --profile root up -d --wait memory-index-postgres hindsight
.venv-pantheon/bin/python3 -m pytest -q deploy/hindsight/tests/test_hindsight_compose_contract.py
```

The contract tests include an opt-in real-container probe. Set
`PANTHEON_HINDSIGHT_SMOKE_COMMAND` to a JSON argv array that executes
`docker compose ... exec -T -w / hindsight python -` in an **isolated** project
with the two services. The test sends its Python probe on stdin, uses the server
key without printing it, retains a unique smoke document, checks Chinese recall,
unauthenticated rejection, MCP disabled and reflect unavailable, and deletes its
bank. `PANTHEON_HINDSIGHT_SMOKE_OUTPUT` optionally records the nonsecret results.
It can prepend the deployment host's pinned-key SSH argv. No product data or
production seed is used. `evidence.json` records the actual host/project used;
this isolated smoke does not switch or certify the currently served FE/BFF pair.

Both services participate in the root deploy's required-worker and exact image /
health receipt checks. Release capture's whole-stack manifest holds their image
IDs under protect tags. A half-pair cannot become a baseline; releases predating
the index (neither service present) remain valid. Rollback restores the held
images using the sealed prior Compose files and retains the named volume.
The fixed three-image BFF artifact bundle is unchanged. A rollback to a release
without these services stops candidate-only containers and leaves the index
volume intact. No rollback path uses `down -v` or treats the index as canonical.

## Drop and rebuild the index

Prefer a fresh index rebuild over trying to reverse an upstream schema migration.
Stop writes/projection through the memory facade first. On the intended dev
project only, stop and remove the two containers, then remove **only**
`<compose-project>_memory-index-postgres-data` after confirming that exact volume
with `docker volume inspect`. Never use `docker compose down -v`, which would
remove canonical volumes. Start the two services at the accepted pinned release;
init and migrations recreate the index. Then run the canonical memory service's
bank rebuild entry point (H3, delivered by `MEMORY-HINDSIGHT-INDEX-20261113`) and
compare active canonical entry counts, tenant/persona isolation and recall before
resuming projection. Until H3 lands, there is no production rebuild command in
this deployment slice. Never reconstruct canonical memory from Hindsight.

Sources: [upstream v1.2.1 image recipe](https://github.com/vectorize-io/hindsight/blob/v1.2.1/docker/standalone/Dockerfile),
[none-provider contract](https://github.com/vectorize-io/hindsight/blob/v1.2.1/hindsight-api-slim/tests/test_none_llm_provider.py),
[PGDG archive](https://apt-archive.postgresql.org/pub/repos/apt/),
[ONNX reranker revision](https://huggingface.co/onnx-community/bge-reranker-v2-m3-ONNX/tree/6f5ff65298512715a1e669753bc754d2bc8f367b).

## Background model calls through the OpenClaw memory-index agent (H5)

Hindsight holds no model key. Its retain, consolidation, reflect and
mental-model-refresh LLM settings can point at the adapter's internal
OpenAI-compatible endpoint `POST /internal/memory-llm/v1/chat/completions`,
which always runs the OpenClaw agent `memory-index` (tools deny `*`, exec
security deny) whatever model name is sent.

- Off by default: adapter `MEMORY_LLM_BRIDGE_ENABLED=false`, Hindsight providers `none`. The same
  `MEMORY_LLM_BRIDGE_ENABLED` also starts Hindsight's worker and sets the memory service's
  `PANTHEON_MEMORY_HINDSIGHT_LLM_LAYERS_ENABLED`; the memory service then switches each bank to verbatim
  retain + consolidation and serves persona standing questions (`MEMORY-LLM-LAYERS-MENTAL-MODELS-20261114`).
  Compose still ships chunks mode and no consolidation as the Hindsight-level defaults.
- Enable: set `MEMORY_LLM_BRIDGE_ENABLED=true`, a random `MEMORY_LLM_BRIDGE_TOKEN`
  (a dedicated token; it is the only credential Hindsight receives, never the
  gateway token), `HINDSIGHT_LLM_BRIDGE_BASE_URL=http://openclaw-gateway-adapter:8104/internal/memory-llm/v1`,
  and `HINDSIGHT_LLM_PROVIDER=openai` plus `HINDSIGHT_*_LLM_PROVIDER=openai` (the default-LLM gate: Hindsight skips consolidation when it is `none`, observed in the real-container probe). Re-run `scripts/openclaw-configure-shared-model-pool.sh`
  so the `memory-index` agent exists.
- Only the internal `memory-llm-bridge` network joins hindsight and the adapter.
- Endpoint refuses `tools`/`tool_choice`/`functions` and streaming (422 with a recorded
  reason), verifies the agent's native-tool policy before every dispatch, and bounds
  each call by deadline (`MEMORY_LLM_BRIDGE_DEADLINE_SECONDS`), concurrency
  (`..._MAX_CONCURRENCY`) and a per-UTC-day budget (`..._DAILY_BUDGET`, persisted).
  Refusals and upstream quota errors return 429 with `Retry-After`, which Hindsight
  treats as a defer. Telemetry carries operation label, latency, token usage and
  outcome, never prompt or response content.
- Models: `OPENCLAW_MEMORY_INDEX_PRIMARY_MODEL` (default `openai/gpt-5.6-sol`) and
  `OPENCLAW_MEMORY_INDEX_FALLBACK_MODELS` (default
  `openai/gpt-5.5,anthropic/claude-opus-4-8,anthropic/claude-sonnet-4-6`; the last is the
  Anthropic CLI backend, acceptable because memory-index needs no tools). Hosted evidence
  2026-11-18: in 576 gateway sessions of 7 days, openai/gpt-5.6-sol answered 615 calls,
  the CLI sonnet backend 6, gpt-5.5 none, and opus-4-8 failed 295 times (invalid key/no credit),
  existing pool slots only; the pool script fails if a ref is not in the pool. Numeric
  limits are coordinator proposals.
- The Hindsight per-operation `*_LLM_BASE_URL` setting names follow the pinned
  1.2.1 naming and must be confirmed in the dev-host run.
- Real-container probe: the opt-in `test_real_container_bridge_probe` in `services/openclaw-gateway-adapter/test_memory_llm_bridge.py` (real hindsight container + real adapter + gateway double).
- Opt-in dev-host probe: `PANTHEON_MEMORY_BRIDGE_PROBE_COMMAND` in
  `services/openclaw-gateway-adapter/test_memory_llm_bridge.py`.
