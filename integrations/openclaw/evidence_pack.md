# OpenClaw Integration — Evidence Pack

Last updated: 2026-06-16
Owner: BP5-OSS-001 (Codex)
Reviewer: Claude
Status: review approved; baseline finalized
Supports: `BP5-OSS-001`

## 1. Purpose

This file consolidates the evidence that `BP5-OSS-001` now locks one reproducible OpenClaw source pin and one governed adapter boundary.

Canonical files:

- `integrations/openclaw/integration.md`
- `integrations/openclaw/governance.md`
- `integrations/openclaw/smoke_test.md`

Supporting files:

- `integrations/openclaw/fixtures/raw_research_handoff.minimal.json`
- `integrations/openclaw/adapter/README.md`
- `scripts/openclaw-smoke-test.sh`

## 2. Locked Upstream Identity

| Evidence item | Value |
|---|---|
| Upstream repository | `https://github.com/openclaw/openclaw` |
| Selected tag | `v2026.6.6` |
| Selected commit | `8c802aa683510c7f7503597b54c3021733245e59` |
| npm package | `openclaw@2026.6.6` |
| Container image | `ghcr.io/openclaw/openclaw:2026.6.6` |
| Container digest | `sha256:4826ca6157377e93463786d5c16852e34eede9f4bd4be55e3773cdc509762857` (multi-arch index) |
| Website / docs | `https://openclaw.ai`, `https://docs.openclaw.ai` |

## 3. Bump Rationale — 2026-04-17 Hold-Pin Context (superseded 2026-06-16)

The baseline was initially held at `v2026.4.7` on 2026-04-17 because:

- `v2026.4.14` had not satisfied the 48-hour soak policy
- the `v2026.4.15-beta.1` prerelease was not eligible for the governed baseline
- the task goal was to pin one stable target for `BP5-OSS-001`

**Bump to `v2026.6.6` (2026-06-16, task OPENCLAW-GOVERNED-BUMP-2026-6-6):**

- `v2026.4.7` only provides localhost-callback paste-back OAuth for OpenAI/Codex accounts — unusable on headless VMs
- `v2026.6.6` adds `openclaw models auth login --provider openai --device-code` (ChatGPT device-code flow), enabling headless subscription-account binding with zero API keys
- auth mode is now subscription OAuth (`openai/oauth`); no `OPENAI_API_KEY` required
- model refs updated to `openai/gpt-5.5`; `openclaw doctor --fix` migrates existing config
- `~/.codex` import removed upstream; volume mounts in `docker-compose.yml` are preserved for compatibility but no longer read by OpenClaw
- dev environment already running `2026.6.6` with account `openai:lupinchen@cctech-support.com`; agent turn confirmed passing end-to-end

## 4. Boundary Evidence

The adapter seam is now documented without claiming unsupported upstream behavior.

Locked conclusions:

- OpenClaw is an external runtime dependency
- Pantheon owns the `openclaw-gateway-adapter`
- Pantheon may define internal `/control/*` facade endpoints later, but those are Pantheon endpoints, not native OpenClaw promises
- governed normalization into `StrategySpec` + `WorkflowHandoff` remains Pantheon-owned

Repo-local evidence:

- runtime / boundary note: `integrations/openclaw/integration.md`
- governance overlay: `integrations/openclaw/governance.md`
- implementation home and guardrails: `integrations/openclaw/adapter/README.md`

## 5. Smoke-Test Evidence

The smoke path is now executable against real, verified surfaces.

Script:

- `scripts/openclaw-smoke-test.sh`

Fixture:

- `integrations/openclaw/fixtures/raw_research_handoff.minimal.json`

Normalization dependency:

- `services/control-plane/specs/normalize_handoff.py`

## 6. Validation Performed

The following checks were rerun locally for `OSS-NEXT-008` on `2026-04-17` (against `v2026.4.7`; see § 3 for original context):

1. `git ls-remote --tags https://github.com/openclaw/openclaw.git` confirmed `v2026.4.7^{}` resolves to `5050017543011b61df67744ebc6368d889c25a95`
2. `docker manifest inspect ghcr.io/openclaw/openclaw:2026.4.7` confirmed the pinned image exists
3. `docker pull ghcr.io/openclaw/openclaw:2026.4.7` resolved the pinned image digest `sha256:be45b5187cbec1ff0f4e2503393d66acfc121c2d97eadf03bb1ac75826bad77c`
4. `docker run --rm --entrypoint node ghcr.io/openclaw/openclaw:2026.4.7 dist/index.js --help` succeeded
5. `docker run --rm --entrypoint node ghcr.io/openclaw/openclaw:2026.4.7 dist/index.js gateway --help` succeeded
6. the governed normalization flow succeeded using the repo-local fixture and canonical schemas
7. `bash scripts/openclaw-gateway-adapter-smoke.sh` passed all four governed workflow checks and wrote `/tmp/openclaw-bp5-oss-002.OMDUTb/smoke-results.json`

Artifact references captured during the refresh:

- baseline smoke work dir: `/tmp/openclaw-bp5-oss-001.gMVtn9`
- live smoke work dir: `/tmp/openclaw-bp5-oss-002.OMDUTb`

## 7. Bump Validation — `v2026.6.6` (2026-06-16, OPENCLAW-GOVERNED-BUMP-2026-6-6)

Checks run against the new baseline:

1. `git ls-remote --tags https://github.com/openclaw/openclaw.git refs/tags/v2026.6.6^{}` → `8c802aa683510c7f7503597b54c3021733245e59` ✓
2. `docker manifest inspect ghcr.io/openclaw/openclaw:2026.6.6` → manifest exists ✓
3. multi-arch index digest: `sha256:4826ca6157377e93463786d5c16852e34eede9f4bd4be55e3773cdc509762857` ✓
4. `bash scripts/openclaw-smoke-test.sh` — **6/6 passed** (2026-06-16): tag resolves, manifest exists, digest matches, `--help` and `gateway --help` succeed, fixture normalizes
5. `bash scripts/openclaw-gateway-adapter-smoke.sh` — **4/4 workflows passed** (2026-06-16): `pantheon.ingest`, `pantheon.review`, `pantheon.retrain`, `pantheon.deploy` all returned PASS against `ghcr.io/openclaw/openclaw:2026.6.6`; gateway_status confirms `"version": "2026.6.6"` RPC

`OPENAI_API_KEY` env status: absent from `docker-compose.yml` `openclaw-gateway` service block (not merely blank — the field does not appear). This is consistent with the 2026.6.6 subscription-OAuth auth model.

## 8. Remaining Work After BP5-OSS-001

This task does not claim full runtime integration.

Still open for `BP5-OSS-002`:

- pick and implement the actual adapter transport
- start a configured OpenClaw gateway in a Pantheon-owned runtime path
- invoke a real workflow through the adapter
- capture live runtime output and prove end-to-end smoke execution
