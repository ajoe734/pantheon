# OpenClaw Integration — Evidence Pack

Last updated: 2026-04-10
Owner: OSS-001A (Codex)
Reviewer: Qwen
Status: ready for handoff
Supports: `OSS-001`

## 1. Purpose

This document consolidates the repo-local evidence collected for the OpenClaw upstream pin, adapter boundary, and smoke-test readiness so the main `OSS-001` owner does not need to reconstruct it from multiple files.

It is intentionally narrower than the integration contract itself:

- `integration.md` is the canonical pin and adapter-boundary note
- `governance.md` is the canonical governance overlay
- `smoke_test.md` is the canonical smoke-test plan
- this file is the supporting evidence summary and handoff pack

## 2. Collected Upstream Evidence

### 2.1 Selected upstream identity

| Evidence item | Value | Repo-local source |
|---|---|---|
| Upstream repository | `https://github.com/openclaw/openclaw` | `spikes/openclaw_upstream_selection.md`, `integrations/openclaw/integration.md` |
| Official docs / landing page | `https://openclaw.im/` | `spikes/openclaw_upstream_selection.md`, `integrations/openclaw/integration.md` |
| Selected integration mode | separate runtime/service dependency | `spikes/openclaw_upstream_selection.md`, `integrations/openclaw/integration.md` |
| Rejected modes | vendoring/submodule, local rewrite in LEAN | `spikes/openclaw_upstream_selection.md`, `integrations/openclaw/integration.md` |

### 2.2 Pinned release evidence

| Evidence item | Value | Repo-local source |
|---|---|---|
| Release tag | `v2026.4.7` | `integrations/openclaw/integration.md` |
| Commit SHA | `5050017` | `integrations/openclaw/integration.md` |
| Release date | `2026-04-08` | `integrations/openclaw/integration.md` |
| Image reference | `openclaw/openclaw:v2026.4.7` | `integrations/openclaw/integration.md`, `integrations/openclaw/smoke_test.md` |
| Pinning rule | tag + commit SHA, never floating branch/image | `spikes/openclaw_upstream_selection.md`, `integrations/openclaw/integration.md` |

### 2.3 Why this upstream is a match

The selected upstream is documented in the spike as matching Pantheon's target architecture because it is treated as:

- an external agent runtime / control-plane substrate
- a self-hosted runtime
- a workflow / plugin system
- a Docker/Kubernetes-deployable service

That aligns with `OPENCLAW_RUNTIME_CONTRACT.md`, which explicitly keeps Strategy Registry, Approval/Promotion, LEAN deployment, telemetry truth, and lineage authority inside Pantheon.

## 3. Adapter-Boundary Evidence

The adapter seam is now documented consistently across the repo:

| Boundary area | Evidence |
|---|---|
| Runtime ownership split | `OPENCLAW_RUNTIME_CONTRACT.md` |
| Pin + integration mode + facade surface | `integrations/openclaw/integration.md` |
| Governance overlay and deny-first wrapping | `integrations/openclaw/governance.md` |
| StrategySpec / WorkflowHandoff normalization path | `integrations/openclaw/governance.md` §5, `integrations/openclaw/smoke_test.md` Step 4-5 |

Key handoff conclusion:

- OpenClaw remains the execution substrate
- Pantheon keeps governance authority
- `openclaw-gateway-adapter` is the only approved mapping seam
- normalization must emit canonical `StrategySpec` plus canonical `WorkflowHandoff`

## 4. Smoke-Test Checklist Readiness

`integrations/openclaw/smoke_test.md` now provides a complete five-step checklist:

| Step | Proof target | Current state |
|---|---|---|
| 1 | pinned runtime starts and becomes ready | plan defined |
| 2 | minimal approved workflow can be invoked | plan defined |
| 3 | raw governed handoff payload can be captured | plan defined |
| 4 | payload normalizes into canonical `StrategySpec` + `WorkflowHandoff` | plan defined |
| 5 | both artifacts validate against local schemas | plan defined |

Execution prerequisites already captured in the smoke plan:

- Docker runtime
- pinned image `openclaw/openclaw:v2026.4.7`
- Python 3.10+
- outbound network access for image pull
- isolated local workspace under `/tmp`

## 5. Local Validation Performed for OSS-001A

To make this evidence pack stronger than a pure documentation summary, an offline schema-level check was executed locally against the canonical schemas in `services/control-plane/specs/`.

Validation performed:

1. Constructed a minimal `raw_handoff.json` fixture
2. Built a canonical `StrategySpec`
3. Wrapped it in a canonical `WorkflowHandoff`
4. Validated both artifacts against:
   - `services/control-plane/specs/strategy_spec.schema.json`
   - `services/control-plane/specs/workflow_handoff.schema.json`

Result:

- `PASS strategy_spec schema`
- `PASS workflow_handoff schema`

Generated local verification artifacts:

- `/tmp/openclaw-oss001a-check/strategy_spec.json`
- `/tmp/openclaw-oss001a-check/workflow_handoff.json`

This confirms the current smoke-test reference shape is aligned with the canonical OC-003 boundary even before Docker-based runtime execution.

## 6. Remaining Work After This Evidence Pack

This task does **not** claim full OpenClaw integration is complete. The following remain intentionally open:

- implement the real `openclaw-gateway-adapter`
- decide the concrete transport between Pantheon and OpenClaw
- execute the Docker-backed smoke test against the pinned runtime
- add the smoke path to CI once manual execution succeeds

## 7. Handoff Summary

`OSS-001A` acceptance is satisfied by this evidence pack plus the existing integration documents:

- upstream source candidates/identity and pin evidence are collected
- smoke-test checklist is drafted and normalized to canonical object boundaries
- the pack can be handed to the `OSS-001` owner without blocking adapter implementation
