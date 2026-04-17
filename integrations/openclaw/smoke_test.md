# OpenClaw Integration — Smoke Test Plan

Last updated: 2026-04-17
Owner: BP5-OSS-002 (Codex)
Reviewer: Claude
Status: baseline and live adapter smoke paths defined
Primary entrypoints:
- `scripts/openclaw-smoke-test.sh`
- `scripts/openclaw-gateway-adapter-smoke.sh`

## 1. Objective

Prove that the locked OpenClaw baseline is real and that Pantheon can execute its governed normalization boundary against it.

For `BP5-OSS-001`, "smoke test" means:

1. the selected Git tag resolves to the expected commit
2. the selected GHCR image exists and exposes the expected CLI / gateway command surface
3. a raw upstream-style handoff fixture normalizes into canonical `StrategySpec` and `WorkflowHandoff`

## 2. Scope Boundary

This smoke plan is intentionally limited to the baseline that exists today.

Included in scope:

- source pin verification
- container artifact verification
- CLI / gateway command-surface verification
- canonical normalization and schema validation

Explicitly out of scope for this task:

- booting a configured OpenClaw gateway with Pantheon-specific runtime settings
- invoking a real workflow against a live adapter
- capturing a real runtime job output over the future Pantheon adapter facade

Those end-to-end checks belong to `BP5-OSS-002`.

## 2.5 BP5-OSS-002 Live Adapter Scope

`BP5-OSS-002` adds the live path that `BP5-OSS-001` left out.

Included in scope now:

- booting a configured upstream gateway container from Pantheon
- probing `/healthz` plus gateway RPC health/status
- dispatching real upstream cron jobs through the Pantheon-side adapter
- observing terminal run status through `cron.runs`
- proving Pantheon-local handoff normalization still happens after live runtime
  execution

## 3. Prerequisites

| Requirement | Notes |
|---|---|
| `git` | required for upstream tag verification |
| Docker Engine 24+ | required unless using `--skip-docker` |
| Python 3.10+ | required for normalization and schema validation |
| `jsonschema` Python package | required by `services/control-plane/specs/normalize_handoff.py` |
| outbound HTTPS | required for GitHub and GHCR access |

Repo-local inputs:

- fixture: `integrations/openclaw/fixtures/raw_research_handoff.minimal.json`
- normalization script: `services/control-plane/specs/normalize_handoff.py`
- schemas:
  - `services/control-plane/specs/strategy_spec.schema.json`
  - `services/control-plane/specs/workflow_handoff.schema.json`

## 4. Canonical Command

Run the full baseline:

```bash
bash scripts/openclaw-smoke-test.sh
```

Run only the repo-local normalization half:

```bash
bash scripts/openclaw-smoke-test.sh --skip-docker
```

Run the live gateway adapter smoke:

```bash
bash scripts/openclaw-gateway-adapter-smoke.sh
```

## 5. What the Script Verifies

### Step 1: Verify the source pin

The script checks:

- `refs/tags/v2026.4.7^{}` resolves to `5050017543011b61df67744ebc6368d889c25a95`

Acceptance:

- the tag resolves exactly to the locked commit

### Step 2: Verify the runtime artifact and command surface

The script checks:

- `ghcr.io/openclaw/openclaw:2026.4.7` is published
- the image digest matches `sha256:be45b5187cbec1ff0f4e2503393d66acfc121c2d97eadf03bb1ac75826bad77c`
- the container can execute:
  - `openclaw --help`
  - `openclaw gateway --help`

Acceptance:

- the image pulls successfully
- both commands exit successfully and print the expected usage banner

### Step 3: Normalize the governed handoff fixture

The script runs:

```bash
python3 services/control-plane/specs/normalize_handoff.py \
  integrations/openclaw/fixtures/raw_research_handoff.minimal.json \
  <work-dir>/normalized
```

Acceptance:

- `strategy_spec.json` is written
- `workflow_handoff.json` is written
- both artifacts validate against the canonical schemas

## 6. Expected Outputs

The script writes its artifacts under a temporary working directory and prints that path at the end.

Expected generated files:

- `<work-dir>/openclaw-help.txt`
- `<work-dir>/openclaw-gateway-help.txt`
- `<work-dir>/normalized/strategy_spec.json`
- `<work-dir>/normalized/workflow_handoff.json`

## 7. Why This Is the Right Baseline

This smoke plan uses only surfaces that were actually verified against the pinned upstream:

- GitHub source tag
- GHCR runtime image
- upstream CLI / gateway command surface
- Pantheon's own canonical normalization script and schemas

It deliberately does **not** pretend that the future Pantheon adapter facade already exists upstream.

## 8. Live Adapter Artifacts

The live smoke writes:

- `<work-dir>/smoke-results.json`

It captures:

- gateway probe output
- upstream cron add/run/runs envelopes
- governed local results for ingest/review/retrain/deploy

Recorded evidence from `2026-04-17`:

- baseline work dir: `/tmp/openclaw-bp5-oss-001.gMVtn9`
- live work dir: `/tmp/openclaw-bp5-oss-002.OMDUTb`
- live result file: `/tmp/openclaw-bp5-oss-002.OMDUTb/smoke-results.json`
- outcome: baseline pin verification passed `6/6`; live gateway smoke passed all four workflows (`pantheon.ingest`, `pantheon.review`, `pantheon.retrain`, `pantheon.deploy`) against `ghcr.io/openclaw/openclaw:2026.4.7`
