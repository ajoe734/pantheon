# OSS and third-party simplification audit — 2026-09-06

Evidence baseline: immutable `origin/dev` snapshot `471dc5391a0f9cbde54d51730891583043708e42`. Earlier working-checkout observations were reconciled to this commit. During the read-only audit: no repository/runtime writes, no package installation, no deployed image inspection, no credentials/.env reads. Publishing these records is a documentation-only change. All versions below are source declarations or upstream release evidence; they do not prove what is installed/deployed.

## Inventory

- 49 requirements.txt files, 195 direct declaration rows, 44 unique package names. 94 rows have no version constraint; 82 ranges; 19 exact pins. These counts include `.orchestrator/requirements.txt` and root test requirements.
- 63 Docker FROM/image declarations and 51 inline pip/CLI-version declarations. This is declaration count, not unique images or services.
- No uv.lock, poetry.lock, Pipfile.lock, pdm.lock, package-lock.json, pnpm-lock.yaml, or yarn.lock in this git tree.
- `pyproject.toml:50` requires Python >=3.11; its dependencies intentionally empty. `pyproject.toml` provisions import paths, not dependency management, and must not be misidentified as a failed package migration.
- `.orchestrator/requirements.txt:17` already provides a separate minimal tooling runtime contract: Pydantic >=2.9,<3, cryptography >=42,<51, packaging >=23,<27. Product/tooling dependency isolation is partly delivered already.
- 22 service requirement files declare each of fastapi, uvicorn, pydantic with no version bound. Additional inline installs include `services/research/Dockerfile:9`, `services/research-worker-gateway/Dockerfile:9`, and `Dockerfile.smoke:14`. Current builds may install newer dependencies than previous builds of identical git source.
- `services/control-plane/bff/requirements.txt:8` and adapter requirements:5 include pytest in runtime requirements: move test dependencies out once runtime startup checks prove unnecessary.

CSV artifacts: `oss-requirements-current-dev.csv`, `oss-images-current-dev.csv`, `oss-inline-installs-current-dev.csv`. The complete 44-package PyPI stable-release inventory is now populated in the requirement CSV and oss-pypi-latest-current-dev.csv/.json. Research-specific feature replacement analysis is in [llm-research-findings.md](llm-research-findings.md).

## Verified upstream latest stable subset

| Component | Current-dev source declaration | Latest verified release (as of audit) | Upstream source |
|---|---|---|---|
| OpenClaw | 2026.7.1; integrations/openclaw/gateway/Dockerfile:17 and services/openclaw-gateway-adapter/Dockerfile:10 | 2026.9.2, published 2026-09-05 20:00 | https://github.com/openclaw/openclaw/releases/tag/v2026.9.2 |
| FastAPI | unbounded in BFF/adapter; >=0.111.0 in 4 other manifests | 0.141.1, 2026-07-29 | https://pypi.org/project/fastapi/ |
| Pydantic | unbounded / >=2.6.3 / >=2.7.0, tooling >=2.9,<3 | 2.13.5, 2026-08-28 | https://pypi.org/project/pydantic/ |
| Uvicorn | unbounded / >=0.29.0 / root >=0.30,<1 | 0.52.4, 2026-08-19 | https://pypi.org/project/uvicorn/ |
| HTTPX | unbounded / >=0.27.0 / >=0.28.0 | 0.28.1, 2024-12-06; 1.0.dev6 is prerelease | https://pypi.org/project/httpx/ |
| Psycopg | unbounded or >=3.1,<4 | 3.3.5, 2026-08-31 | https://pypi.org/project/psycopg/ |
| asyncpg | >=0.29,<1 (or unbounded upper) | 0.31.0, 2025-11-24 | https://pypi.org/project/asyncpg/ |
| nats-py client | >=2.9,<3 | 2.15.0, 2026-06-05 | https://pypi.org/project/nats-py/ |
| redis Python client | >=5.0.0 / >=5.0.4 | 8.1.0, 2026-07-30 | https://pypi.org/project/redis/ |
| PostgreSQL server | postgres:16-alpine; docker-compose.yml:11 | 18.6 / maintained 16-series 16.15, both 2026-08-13 | https://www.postgresql.org/docs/release/18.6/ ; https://www.postgresql.org/docs/release/16.15/ |
| Redis server | redis:7-alpine main/exec; remote-dev redis:alpine | 8.10.1, August 2026 | https://redis.io/docs/latest/operate/oss_and_stack/stack-with-enterprise/release-notes/redisce/redisos-8.10-release-notes/ |
| NATS server | nats:2.11-alpine; docker-compose.yml:74 | 2.14.5, 2026-08-12 | https://github.com/nats-io/nats-server/releases/tag/v2.14.5 |
| Python | most images python:3.11-slim; broker and registry test 3.12-slim | 3.14.7, 2026-08-05 | https://www.python.org/downloads/release/python-3147/ |
| MinIO | digest 14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e; server version behind this digest not resolved | Community repo archived 2026-04-25; no longer maintained, source-only distribution; legacy binaries unmaintained | https://github.com/minio/minio |

No latest stable claim was made for MinIO client, BusyBox, or model-provider CLIs. All 44 direct Python package latest stable versions were subsequently verified by the official PyPI JSON API; see full table below. PostgreSQL 19 beta and HTTPX 1.0.dev6 are not stable upgrade targets. Stable version and Python compatibility are separate questions; framework-declared Python >=3.10 (FastAPI/Uvicorn) permits 3.11, and Python 3.14 is not a prerequisite for their SSE simplification.

## Supported deletion opportunities

### 1. FastAPI-native SSE replaces duplicate wire-framing/heartbeat boilerplate — high confidence, bounded change

Current-dev: `services/control-plane/bff/events/service.py:143` `format_event`, `:211` stream, `:226` heartbeat, `:270` StreamingResponse. `services/control-plane/bff/events/router.py:157` second formatter, `:185` second stream, `:206` heartbeat, `:231` StreamingResponse. Adapter `services/openclaw-gateway-adapter/main.py:1629-1712` manually frames SSE and [DONE].

Upstream FastAPI 0.135.0 (2026-03-01) added EventSourceResponse/ServerSentEvent; current native implementation serializes fields, supplies keepalive pings and cache/proxy headers. Sources: https://fastapi.tiangolo.com/release-notes/#01350 ; https://fastapi.tiangolo.com/tutorial/server-sent-events/ .

Delete only framing/serialization/ping/header boilerplate after exact envelope, [DONE], disconnect, multiline data, last-event-id/replay tests. Keep application event buffers/replay authorization, tenant filtering, queue bounds and 409 resync semantics. Native SSE does not replace business event history.

### 2. Remove explicit Pydantic v1 branches — high confidence after unified v2 dependency baseline

Current-dev: `services/consultation/store.py:38-59` contains four v1/v2 dump/copy/validate/json wrappers; `services/consultation/main.py:188` dump branch and `:224` explicit v1 model_fields/__fields__ fallback; `services/openclaw-gateway-adapter/consultation_provider.py:208-211` dump fallback. There are similar serializer branches elsewhere, but not every hasattr(model_dump) is v1 compatibility (some intentionally accept dictionaries/dataclasses).

FastAPI 0.126 mandates Pydantic v2, 0.128 removed pydantic.v1 support. Current upstream Pydantic methods model_dump, model_copy, model_validate, model_fields cover these branches. Sources: https://fastapi.tiangolo.com/release-notes/#01280 ; https://pydantic.dev/docs/validation/latest/get-started/migration/ .

Preserve serialization semantics in stored records/idempotency fingerprints: date/enums/null handling and JSON canonicalization may affect historical data. No broad deleting of jsonschema: it may own independent cross-service contracts.

### 3. Consolidate agent-turn transports — promising, requires behavior proof

`services/openclaw-gateway-adapter/assistant_openclaw_provider.py:413` invokes CLI per normal turn; `:582` diverts large argv prompts to `:644` HTTP workaround; `:906` already streams over Responses. OpenResponses docs state it runs the same gateway agent path, supporting agent/model selection, session routing, client function tools, streaming and previous_response_id: https://docs.openclaw.ai/gateway/openresponses-http-api .

Unify normal agent turns on one HTTP client to retire per-turn subprocess/result parsing and argv size escape hatch. This capability partly exists in the currently pinned OpenClaw, so do not present all transport cleanup as requiring 2026.9.2. Native upgrade adds model/session/recovery reliability, but mapping Pantheon's operator/session/profile semantics is still needed.

Do not claim removal of entire copied OpenClaw/Node runtime: the same module still invokes gateway cron/agent administration at :713,:728,:741. Provider CLIs required for subscription auth must remain unless an equivalent supported route is proven.

OpenClaw 2026.9.2 documents GPT-6 Astra available via API-key or eligible ChatGPT/Codex account. Separate async-tools/steering features are expressly limited to API-key routes using official Responses. Do not imply subscription feature parity. This release was published Sep 5 20:00 and has not met the repo's >=48h release soak (`integrations/openclaw/integration.md:51`) on Sep 6; latest published and immediately eligible production baseline differ.

### 4. Remove unused dependencies before upgrading them — high-confidence static candidates

`services/control-plane/bff/requirements.txt:6-7` declares `python-jose[cryptography]` and `passlib[bcrypt]`. `git grep -n -E 'jose|passlib|bcrypt' origin/dev -- services .github scripts .orchestrator` returns exactly those two lines, no application/test imports. Remove after clean-image startup, auth-route and dependency-closure checks, rather than upgrading unused packages. Directly declare any cryptography dependency used by actual auth code if it currently arrives transitively; verify that before removal.

### 5. MinIO lifecycle decision can reduce infrastructure — needs topology decision

Main/control compose pin a server digest, while clients diverge: main `minio/mc:RELEASE.2024-01-16T16-06-34Z` (`docker-compose.yml:57`) and control `minio/mc:latest` (`docker-compose.control.yml:82`). Upstream archive/source-only state means “upgrade MinIO to latest” does not solve maintained artifact ownership.

Potential simplification: use one maintained object-store backend (managed storage or a selected maintained S3 implementation) and retire local server/init operations where not needed. Preserve object durability, retention, artifact references and test seams; do not delete S3 ports or migrate data solely on this audit. Current BFF already uses GCS for selected attachment storage (`management_ai_store.py:190`), which is not evidence that all S3 workloads can automatically move to GCS.

### 6. Shared constraints/locks replace drift, not isolated research environments

Adopt one tested core API dependency baseline, per-runtime resolved locks and digest-backed images to remove duplicated inline version selection. Keep dedicated tooling interpreter and isolated numerical/ML containers where dependency sets conflict; do not merge the monorepo into one giant environment. Scientific versions in separate services are not automatically conflicting runtime installs.

## Deliberately not claimed removable

- PostgreSQL 18, Redis 8 and NATS 2.14 updates do not by themselves eliminate business outboxes, idempotency, audit trails, capital boundaries, broker truth or replay semantics. No direct upstream-equivalence proof found for wholesale deletion.
- `paper_fleet_reconciler.py:166-223` Lua lease includes server-time expiration, monotonic fencing tokens and ownership checks. Redis 8.4's simpler compare-and-delete is not a drop-in replacement for this contract.
- `execution/lean_runtime/pending_signal_store.py` has custom claim/visibility/reclaim queue logic. Redis Streams/NATS may reduce implementation, but require crash/delivery/idempotency migrations. Some primitives existed before current versions; classify as consolidation, not a new-LLM feature.
- Source manifests cannot identify deployed patch levels for floating postgres:16-alpine, redis:7-alpine, nats:2.11-alpine or Python slim tags. Export actual image IDs/SBOM/pip metadata from the accepted deployment before asserting outdated installed versions.

## Complete PyPI metadata verification

Read-only requests to https://pypi.org/pypi/{package}/json and versioned package JSON endpoints. Chosen versions exclude prereleases, development releases, and yanked-only releases; included artifacts were published before 2026-09-07T00:00:00Z. No install/build/solver occurred. `requires_python` eligibility does not prove wheel availability, compiled-library compatibility, or transitive dependency compatibility.

27 declaration rows across 19 package names exclude the latest PyPI stable version. This is a manifest comparison, not proof of outdated deployed versions.

| Package | Latest stable | First eligible upload date | Requires Python | Official release |
|---|---|---|---|---|
| accelerate | 1.14.0 | 2026-06-11 | >=3.10.0 | [PyPI](https://pypi.org/project/accelerate/1.14.0/) |
| asyncpg | 0.31.0 | 2025-11-24 | >=3.9.0 | [PyPI](https://pypi.org/project/asyncpg/0.31.0/) |
| cryptography | 50.0.1 | 2026-08-25 | !=3.9.0,!=3.9.1,>=3.9 | [PyPI](https://pypi.org/project/cryptography/50.0.1/) |
| datasets | 5.0.1 | 2026-07-28 | >=3.10.0 | [PyPI](https://pypi.org/project/datasets/5.0.1/) |
| discord.py | 2.7.1 | 2026-03-03 | >=3.8 | [PyPI](https://pypi.org/project/discord.py/2.7.1/) |
| dspy-ai | 3.3.1 | 2026-08-21 | >=3.9 | [PyPI](https://pypi.org/project/dspy-ai/3.3.1/) |
| fastapi | 0.141.1 | 2026-07-29 | >=3.10 | [PyPI](https://pypi.org/project/fastapi/0.141.1/) |
| finrl | 0.3.7 | 2024-04-12 | >=3.7 | [PyPI](https://pypi.org/project/finrl/0.3.7/) |
| flask | 3.1.3 | 2026-02-19 | >=3.9 | [PyPI](https://pypi.org/project/flask/3.1.3/) |
| google-cloud-storage | 3.13.1 | 2026-08-06 | >=3.10 | [PyPI](https://pypi.org/project/google-cloud-storage/3.13.1/) |
| gymnasium | 1.3.0 | 2026-04-22 | >=3.10 | [PyPI](https://pypi.org/project/gymnasium/1.3.0/) |
| httpx | 0.28.1 | 2024-12-06 | >=3.8 | [PyPI](https://pypi.org/project/httpx/0.28.1/) |
| imitation | 1.0.1 | 2025-01-07 | >=3.8.0 | [PyPI](https://pypi.org/project/imitation/1.0.1/) |
| jsonschema | 4.26.0 | 2026-01-07 | >=3.10 | [PyPI](https://pypi.org/project/jsonschema/4.26.0/) |
| lightgbm | 4.7.0 | 2026-07-18 | >=3.10 | [PyPI](https://pypi.org/project/lightgbm/4.7.0/) |
| matplotlib | 3.11.1 | 2026-07-18 | >=3.11 | [PyPI](https://pypi.org/project/matplotlib/3.11.1/) |
| mlflow | 3.16.0 | 2026-09-04 | >=3.10 | [PyPI](https://pypi.org/project/mlflow/3.16.0/) |
| nats-py | 2.15.0 | 2026-06-05 | >=3.7 | [PyPI](https://pypi.org/project/nats-py/2.15.0/) |
| numpy | 2.5.2 | 2026-08-09 | >=3.12 | [PyPI](https://pypi.org/project/numpy/2.5.2/) |
| packaging | 26.3 | 2026-08-04 | >=3.9 | [PyPI](https://pypi.org/project/packaging/26.3/) |
| pandas | 3.0.5 | 2026-07-22 | >=3.11 | [PyPI](https://pypi.org/project/pandas/3.0.5/) |
| passlib | 1.7.4 | 2020-10-08 | metadata unspecified | [PyPI](https://pypi.org/project/passlib/1.7.4/) |
| peft | 0.20.0 | 2026-07-28 | >=3.10.0 | [PyPI](https://pypi.org/project/peft/0.20.0/) |
| plotly | 7.0.0 | 2026-08-25 | >=3.8 | [PyPI](https://pypi.org/project/plotly/7.0.0/) |
| psycopg | 3.3.5 | 2026-08-31 | >=3.10 | [PyPI](https://pypi.org/project/psycopg/3.3.5/) |
| pydantic | 2.13.5 | 2026-08-28 | >=3.9 | [PyPI](https://pypi.org/project/pydantic/2.13.5/) |
| pyqlib | 0.9.7 | 2025-08-15 | >=3.8.0 | [PyPI](https://pypi.org/project/pyqlib/0.9.7/) |
| pytest | 9.1.1 | 2026-06-19 | >=3.10 | [PyPI](https://pypi.org/project/pytest/9.1.1/) |
| python-jose | 3.5.0 | 2025-05-28 | >=3.9 | [PyPI](https://pypi.org/project/python-jose/3.5.0/) |
| python-multipart | 0.0.32 | 2026-06-04 | >=3.10 | [PyPI](https://pypi.org/project/python-multipart/0.0.32/) |
| python-telegram-bot | 22.8 | 2026-06-12 | >=3.10 | [PyPI](https://pypi.org/project/python-telegram-bot/22.8/) |
| QuantLib-Python | 1.18 | 2020-03-23 | metadata unspecified | [PyPI](https://pypi.org/project/QuantLib-Python/1.18/) |
| ray | 2.58.0 | 2026-08-23 | >=3.10 | [PyPI](https://pypi.org/project/ray/2.58.0/) |
| redis | 8.1.0 | 2026-07-30 | >=3.10 | [PyPI](https://pypi.org/project/redis/8.1.0/) |
| sentencepiece | 0.2.2 | 2026-07-12 | >=3.9 | [PyPI](https://pypi.org/project/sentencepiece/0.2.2/) |
| shioaji | 1.7.4 | 2026-08-27 | >=3.7 | [PyPI](https://pypi.org/project/shioaji/1.7.4/) |
| stable-baselines3 | 2.9.0 | 2026-06-15 | >=3.10 | [PyPI](https://pypi.org/project/stable-baselines3/2.9.0/) |
| statsmodels | 0.15.0 | 2026-08-27 | >=3.10 | [PyPI](https://pypi.org/project/statsmodels/0.15.0/) |
| torch | 2.14.0 | 2026-09-02 | >=3.10 | [PyPI](https://pypi.org/project/torch/2.14.0/) |
| transformers | 5.16.1 | 2026-08-26 | >=3.10.0 | [PyPI](https://pypi.org/project/transformers/5.16.1/) |
| trl | 1.12.0 | 2026-08-26 | >=3.10 | [PyPI](https://pypi.org/project/trl/1.12.0/) |
| uvicorn | 0.52.4 | 2026-08-19 | >=3.10 | [PyPI](https://pypi.org/project/uvicorn/0.52.4/) |
| vectorbt | 1.1.0 | 2026-07-05 | <3.15,>=3.11 | [PyPI](https://pypi.org/project/vectorbt/1.1.0/) |
| wandb | 0.29.0 | 2026-08-26 | >=3.10 | [PyPI](https://pypi.org/project/wandb/0.29.0/) |

Key compatibility finding: NumPy 2.5.2 requires Python >=3.12, while ML images are Python 3.11. A blanket latest upgrade is therefore impossible without an interpreter decision. Keep a supported Python-3.11-compatible NumPy or upgrade the relevant numerical images together; do not jump directly to Python 3.14 across all old numerical packages.

FinRL 0.3.7, imitation 1.0.1 and QuantLib-Python 1.18 are already the latest stable **distribution names** on PyPI. Old release dates alone do not show a newer version exists. QuantLib-Python is an old compatibility package; the actual maintained QuantLib distribution is 1.43, released 2026-07-14 (https://pypi.org/project/QuantLib/). Replace the metapackage with an explicit tested QuantLib pin if adopting the maintained direct dependency; do not imply the currently resolved QuantLib library must be 1.18.

The Torch declaration includes a +cpu local version and an extra PyTorch index. The inventory checks PyPI Torch 2.14.0 release identity only; availability of the corresponding CPU wheel/index artifact remains unverified.
