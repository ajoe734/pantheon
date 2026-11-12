# OSS Current Version Evidence — 2026-11-12

Task: OSS-CURRENT-VERSION-EVIDENCE-20261112 · Owner: Claude · Reviewer: Codex
Observed: 2026-11-12T02:31:46.725549+00:00 (read-only official PyPI/npm lookups; no package installed, no runtime change)

This is a bounded version report. It is **not** hosted-version proof, not a product acceptance, and does not authorize any upgrade.

## Scope and pinned sources

| Item | Value |
|---|---|
| Backend | `ajoe734/pantheon` @ `e28bef7e430c1f6da8d4448806e977bfec92ea42` (tree `2237de4f…`) |
| Frontend | `ajoe734/execute-plans` @ `aa17cd4fc390e2c311ef50cbd5f7d2160faea775`; package.json blob `1f2021e9cae4bd3973982823978cf457d6379c0c`, package-lock.json blob `a5b2f8f7ddbb50b3e74c5f1d63ab7d25d419fe22` |
| Python | 56 manifest files; 504 declarations (144 are lock declarations); 232 unique distributions |
| npm | 90 direct dependencies with exact lock versions |
| Registries | `https://pypi.org/pypi/<name>/json`, `https://registry.npmjs.org/<name>/latest` (per-package URLs in evidence.json) |
| Lookup errors | 0 |

Input SHA256 (verified): audit `454e2f23eb7258d74ad277dff4c724b4fdfc58a14400ab45ba56b913f293058b`, summary `a40bdf83c51ed5d6fa29fd241b18f9d08fa591a4d8bf5156be2e94ea101667ae`, original-19 dispositions `45b557fda1ae94268cf607d0fe488e365821f52992f10aeacc6a5a8715c488f3`. Backend manifest files all exist at the pinned commit; frontend blobs match the GitHub tree for the pinned commit.

Backend declaration coverage (504, includes lock declarations) differs from the older 126-package audit; do not compare those counts as identical coverage.

## Findings

- **Only-prerelease result:** `opentelemetry-semantic-conventions` publishes only `0.66b2` (prerelease). It has no stable version, so it is *not* reported as "latest stable".
- **npm:** 80 of 90 direct dependencies have a locked version different from registry latest; 33 of those are major-version differences (list below).
- **Python:** 156 per-declaration rows (93 distinct packages) have a declared constraint that excludes the registry latest. Comparison is per declaration, not whole-environment resolution.
- Differences are not claimed to be all direct production dependencies (npm includes devDependencies) nor automatically safe upgrades.

### npm major-version differences (33)

| Package | Locked | Latest |
|---|---|---|
| `@streamdown/code` | 1.1.1 | 2.0.0 |
| `ai` | 6.0.197 | 7.0.170 |
| `date-fns` | 3.6.0 | 4.4.0 |
| `firebase` | 12.16.0 | 13.3.0 |
| `i18next` | 26.0.10 | 27.1.1 |
| `motion` | 12.40.0 | 14.12.0 |
| `nanoid` | 5.1.11 | 6.0.2 |
| `react` | 18.3.1 | 19.3.1 |
| `react-day-picker` | 8.10.1 | 10.0.2 |
| `react-dom` | 18.3.1 | 19.3.1 |
| `react-grid-layout` | 1.5.3 | 2.3.0 |
| `react-i18next` | 17.0.7 | 18.0.1 |
| `react-resizable-panels` | 2.1.9 | 4.21.0 |
| `react-router-dom` | 6.30.4 | 7.18.5 |
| `recharts` | 2.15.4 | 3.10.1 |
| `sonner` | 1.7.4 | 2.0.8 |
| `tailwind-merge` | 2.6.0 | 3.7.0 |
| `vaul` | 0.9.9 | 1.1.2 |
| `zod` | 3.25.76 | 4.6.7 |
| `@eslint/js` | 9.32.0 | 10.0.1 |
| `@testing-library/jest-dom` | 6.9.1 | 7.0.1 |
| `@types/node` | 22.20.3 | 26.6.6 |
| `@types/react` | 18.3.23 | 19.3.0 |
| `@types/react-dom` | 18.3.7 | 19.3.0 |
| `@types/react-grid-layout` | 1.3.6 | 2.1.0 |
| `@vitejs/plugin-react-swc` | 3.11.0 | 4.3.3 |
| `eslint` | 9.32.0 | 10.14.0 |
| `eslint-plugin-react-hooks` | 5.2.0 | 7.1.1 |
| `globals` | 15.15.0 | 17.14.0 |
| `jsdom` | 20.0.3 | 31.0.0 |
| `typescript` | 5.9.3 | 7.0.2 |
| `vite` | 5.4.21 | 8.3.8 |
| `vitest` | 3.2.7 | 5.1.0 |

The other 47 npm differences are same-major (minor/patch) and are listed in `docs/reviews/2026-11-12-oss-current-version-audit.json` (`npm_locked_differences`) and per-package in evidence.json.

### Python packages with at least one declaration excluding latest (93)

`aiohttp`, `alembic`, `annotated-doc`, `anyio`, `asyncpg`, `cachetools`, `certifi`, `charset-normalizer`, `click`, `contourpy`, `cryptography`, `databricks-sdk`, `datasets`, `docling`, `docling-core`, `docling-ibm-models`, `docling-parse`, `dspy-ai`, `fastapi`, `fastembed`, `fonttools`, `gitpython`, `google-auth`, `graphql-core`, `gunicorn`, `gymnasium`, `httptools`, `huey`, `huggingface-hub`, `importlib-metadata`, `lxml`, `markupsafe`, `mlflow`, `mlflow-skinny`, `mlflow-tracing`, `mpmath`, `multidict`, `narwhals`, `networkx`, `numpy`, `onnxruntime`, `opentelemetry-api`, `opentelemetry-proto`, `opentelemetry-sdk`, `pandas`, `pgvector`, `pillow`, `playwright`, `plotly`, `protobuf`, `psycopg`, `psycopg-binary`, `pyarrow`, `pyasn1`, `pycparser`, `pydantic`, `pydantic-core`, `pyee`, `pypdfium2`, `pytest`, `python-dateutil`, `python-dotenv`, `qdrant-client`, `quantlib`, `ray`, `redis`, `scipy`, `scrapling`, `semchunk`, `sentencepiece`, `soupsieve`, `sqlalchemy`, `sqlparse`, `stable-baselines3`, `starlette`, `threadpoolctl`, `tifffile`, `tokenizers`, `torch`, `torchvision`, `transformers`, `trl`, `typer`, `typing-inspection`, `uvicorn`, `uvloop`, `vectorbt`, `watchfiles`, `wcwidth`, `websockets`, `werkzeug`, `wtforms`, `zipp`

## Dispositions of observed differences

No upgrade, wrapper, compatibility layer, audit framework or per-difference task is created here. Evidence: `docs/deployment/evidence/OSS-CURRENT-VERSION-EVIDENCE-20261112/evidence.json` (per-package `disposition`).

| Group | Count | Disposition |
|---|---|---|
| npm same-major lock behind latest | 47 | Candidate, not scheduled (possible lock refresh) |
| npm major differences | 33 | Unknown compatibility; needs a separately scoped review before being a candidate (React 18 line, Vite/Vitest/TypeScript toolchain, router/charts/zod majors are coupled) |
| Python declarations excluding latest | 156 rows / 93 packages | Existing supported baseline; intent of each bound and compatibility with latest unverified |
| `opentelemetry-semantic-conventions` | 1 | Prerelease only; existing baseline, no stable target |
| No difference observed | remaining | None |

## Original 19-task reconciliation

Source: historical materialized packet `pkt-llm-oss-simplification-execution-20260906-v2` (not a tracked file at current dev, sha256 `2b253038baa20bfb542f38f490253644461687aa8c493c1a17c0af31248f2b01`), reconciled with native terminal facts and archive receipts in the coordination root: **12 completed, 7 superseded**.

| Task | Outcome | Terminal fact (gen, recorded) | Archive snapshot |
|---|---|---|---|
| OSS-COVERAGE-PLAN-001 | completed | 2, 2026-09-06T06:48:08Z | `be0e57efb7d7…` |
| OSS-CORE-BASELINE-001 | completed | 248, 2026-09-21T00:54:02Z | `5031a887010e…` |
| OSS-TOOLING-BASELINE-001 | completed | 4, 2026-09-06T12:17:18Z | `5aa1f14172b7…` |
| OSS-RESEARCH-FOOTPRINT-001 | superseded | 139, 2026-09-29T15:26:14Z | `f937f4bbb71a…` |
| OSS-RESEARCH-BASELINE-001 | superseded | 16, 2026-09-29T15:26:19Z | `7fe4f1c32124…` |
| OSS-EXECUTION-BASELINE-001 | completed | 229, 2026-09-21T01:32:56Z | `3574122b1462…` |
| FE-DEP-CLOSURE-001 | completed | 12, 2026-09-09T16:01:30Z | `eecdfb7d7443…` |
| FE-TOOLCHAIN-BASELINE-001 | completed | 61, 2026-09-17T17:25:11Z | `8e298e2a3460…` |
| FE-UI-DEPS-BASELINE-001 | completed | 11, 2026-09-17T18:49:38Z | `4584fd719432…` |
| OSS-INFRA-CHOICE-001 | completed | 4, 2026-09-06T08:17:16Z | `ee511eaf7fab…` |
| OSS-INFRA-PROFILES-001 | superseded | 58, 2026-09-29T15:26:25Z | `59fd16617f31…` |
| OSS-OBJECT-STORE-CUTOVER-001 | superseded | 48, 2026-09-29T15:26:30Z | `62031d1885dc…` |
| SIMPLIFY-OPENCLAW-001 | completed | 21, 2026-09-06T18:02:32Z | `1605854e50ad…` |
| SIMPLIFY-EXTRACTION-001 | superseded | 182, 2026-09-29T15:26:09Z | `c57f352016b3…` |
| SIMPLIFY-RETRIEVAL-MEMORY-001 | completed | 20, 2026-09-09T05:45:04Z | `e3a9167e955e…` |
| SIMPLIFY-BFF-RESIDUAL-001 | superseded | 202, 2026-09-29T15:26:03Z | `62a00ff10005…` |
| FE-QUERY-CACHE-001 | completed | 99, 2026-09-17T13:22:42Z | `caa48e5bcbe8…` |
| OPS-LEGACY-REVIEW-RETIRE-001 | completed | 2, 2026-09-06T11:31:02Z | `3f94c0d07ca3…` |
| SYS-SIMPLIFY-IMPLEMENTATION-CLOSURE-001 | superseded | 45, 2026-09-29T15:26:35Z | `4e9d73402a51…` |

The 7 superseded tasks (OSS-RESEARCH-FOOTPRINT-001, OSS-RESEARCH-BASELINE-001, OSS-INFRA-PROFILES-001, OSS-OBJECT-STORE-CUTOVER-001, SIMPLIFY-EXTRACTION-001, SIMPLIFY-BFF-RESIDUAL-001, SYS-SIMPLIFY-IMPLEMENTATION-CLOSURE-001) **remain retired**. Each archive receipt records the same meaning: superseded by operator replan (Human/Ops, 2026-09-29) of the BFF simplification chain after 67 review rejects and frozen draft PR #5998; replacement work to come as new operator-approved tasks; do not recreate or resume. No named successor task is recorded, and superseded is not treated as evidence of implementation.

Historical completion does not prove every package is currently latest, nor that all 16 feature candidates passed product acceptance.

## Coverage limits and gaps

- Backend Python manifests, optional and build dependencies only; shell/workflow/Docker inline installs not included.
- npm direct manifest declarations and exact lock entries only; transitive dependencies are not all audited.
- Container images and Actions releases are not covered by this package audit.
- Manifest and lock comparisons are not installed or hosted version proof; no package installation performed.
- Python comparison is per declaration; aggregate environment resolution and include/constraint semantics are not proved.
- Backend manifest declaration coverage (504 incl. 144 lock declarations) differs from the older 126-package audit because this snapshot includes lock declarations; counts are not comparable as identical coverage.
- Installed or hosted versions, include/constraint semantics, and transitive npm dependencies are unverified.

Remaining gaps: inline installs, container images, Actions, transitive npm dependencies, installed/hosted versions and include/constraint semantics are unaudited. Owner handoff, exact-head review by Codex, required CI and existing integration remain required; source report delivery is separate from product deployment and acceptance.

## Validation

Actual results (2026-11-12):

- Input SHA256 identities, 56 manifest files at the pinned backend commit, and frontend package/lock blobs: verified, all match.
- Both JSON files parse; counts (90 npm, 232 Python, 80 npm differences, 33 major, 156 exclusions) are consistent between the report JSON, evidence.json and the supplied summary: passed.
- `git diff --check`: clean.
- `python3 scripts/audit_canonical_docs.py`: reports 1 broken canonical reference (`ai-activity-log.jsonl` missing in this task worktree). It is unrelated to these files, which it does not reference; not fixed here.
- The temporary audit helper and its /tmp inputs are not committed.
