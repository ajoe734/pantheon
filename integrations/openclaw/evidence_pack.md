# OpenClaw Integration — Evidence Pack

Last updated: 2026-04-15
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
| Selected tag | `v2026.4.7` |
| Selected commit | `5050017543011b61df67744ebc6368d889c25a95` |
| npm package | `openclaw@2026.4.7` |
| Container image | `ghcr.io/openclaw/openclaw:2026.4.7` |
| Container digest | `sha256:be45b5187cbec1ff0f4e2503393d66acfc121c2d97eadf03bb1ac75826bad77c` |
| Website / docs | `https://openclaw.ai`, `https://docs.openclaw.ai` |

## 3. Hold-Pin Rationale as of 2026-04-15

Current upstream release state at verification time:

- latest stable: `v2026.4.14`, published `2026-04-14`
- latest prerelease: `v2026.4.15-beta.1`, published `2026-04-15`

Why the baseline still stays on `v2026.4.7`:

- the repo's 48-hour soak rule is not yet satisfied for `v2026.4.14`
- the beta tag is not eligible for the governed baseline
- the task goal is to pin one stable target before `BP5-OSS-002` implements the real adapter path

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

The following checks were run locally for this task:

1. `git ls-remote --tags https://github.com/openclaw/openclaw.git` confirmed `v2026.4.7^{}` resolves to `5050017543011b61df67744ebc6368d889c25a95`
2. `docker manifest inspect ghcr.io/openclaw/openclaw:2026.4.7` confirmed the pinned image exists
3. `docker pull ghcr.io/openclaw/openclaw:2026.4.7` resolved the pinned image digest `sha256:be45b5187cbec1ff0f4e2503393d66acfc121c2d97eadf03bb1ac75826bad77c`
4. `docker run --rm --entrypoint node ghcr.io/openclaw/openclaw:2026.4.7 dist/index.js --help` succeeded
5. `docker run --rm --entrypoint node ghcr.io/openclaw/openclaw:2026.4.7 dist/index.js gateway --help` succeeded
6. the governed normalization flow succeeded using the repo-local fixture and canonical schemas

## 7. Remaining Work After BP5-OSS-001

This task does not claim full runtime integration.

Still open for `BP5-OSS-002`:

- pick and implement the actual adapter transport
- start a configured OpenClaw gateway in a Pantheon-owned runtime path
- invoke a real workflow through the adapter
- capture live runtime output and prove end-to-end smoke execution
